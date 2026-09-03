# run-gate resumable-gate wave (E-1 → 23.4.0) — controller log

Rulings RW-1..RW-8 are in the wave prompt
(`WAVE-PROMPT-2026-09-02-resumable-gate.md`). This file carries the
controller's entries from the implementer's return onward; wave records
(`-LOG`, `-REPORT`, `-REVIEW-round<n>`) live on the branch
`feature/run-gate-wave-resumable`.

- **2026-09-02 (implementer returned — all four items landed, gate GREEN;
  RW-9..RW-12; follow-up package before the reviewer)** — Verified in a
  separate step from `selftest-close.log`: `488 passed, 2 skipped`,
  `diff-coverage OK: 269/269 changed executable lines covered (100.0% ≥
  100.0% floor)`, `lane 'selftest' exit 0`; `git log main..HEAD` = five
  commits (`6fe633f5` RG-35/R-39, `8db781e6` RG-32/R-08a BREAKING,
  `1e41069f` RG-34/R-30b, `10aa59e2` RG-36/R-40, `d4e8e137` records +
  RG-39 filed), tree clean, `__revision__ = 34`. Red-first for RG-35 was
  real: rev 33 on a detached worktree starts two `docker run -d` for one
  lane/commit (`assert 2 == 1`). The live acceptance probe ran against a
  real `tester-unified:local` at 21:54 UTC (waited for Wave D's container,
  `--cpus=3`, trap-removed): client SIGKILLed 8 s in, container survived,
  invocation 2 printed `re-attached to … (started 2026-09-02T21:54:24Z,
  running for 0m 08s)`, started no container, replayed from `tick 1`
  (`--since` proven), exit 0, record cleared, ONE history entry with
  `duration_seconds: 70.858` from the container's clock (RW-3 proven live).
  The implementer deliberately continued past the E-008 arm point (~60
  calls after RG-34's green gate) and finished RG-36 without a BRIEF; the
  deviation is recorded in its LOG.
  - **RW-9 (ask 1 — `stall_timeout` on a `kind = "command"` lane): the
    refusal stands.** RW-5's signal is the progress file; a key that can
    never act on a command lane is exactly RG-32's defect class, and
    run-gate does not guess a second signal on its own. The gap is real
    (container command lanes are the shape most likely to hang) and gets
    its own item: **RG-40 — liveness for a container command lane judged
    from LOG-STREAM silence.** run-gate already streams `docker logs -f`;
    the arrival time of the last line on run-gate's own clock carries the
    same "silence, never elapsed" semantics R-40 gives the progress file,
    so `stall_timeout` becomes legal on command lanes with the SOURCE of
    the signal disclosed at start (`progress file` vs `log stream`).
    E-3 candidate (23.5.0). Filed on the branch by the implementer in the
    follow-up package so the reviewer's tip carries it and the merge has
    no backlog conflict.
  - **RW-10 (ask 2 — `--fresh` through a conjunction): no propagation, no
    token; document the shape.** `--fresh` is per-invocation and
    deliberately does NOT fan out: fanning it out would remove EVERY
    sub-lane's container including ones legitimately running, the blunt
    tool RG-35 exists to avoid. A sub-lane's commit-mismatch refusal
    already exits 2 through the conjunction's `&&` chain naming THAT
    sub-lane and `--fresh`, and the operator applies it to that sub-lane
    directly. A consumer who wants a sub-lane always fresh writes
    `--fresh` into that sub-invocation's static argv, forfeiting re-attach
    for it, and the docs say so. One paragraph in CONSUMERS.md's
    "Gate-conjunction lanes" section beside the RG-1/`{base}` sentences,
    one line under SPEC R-39.
  - **RW-11 (ask 3 — RG-39, `coverage_gate.py` dirty-tree offsets): filed
    stands, not fixed in this wave.** Every measurement in the wave was
    taken on a committed tip at 100.0%, so the evidence stands. The fix
    (changed lines diffed against the WORKING TREE when `--allow-dirty`,
    plus its test) is the first item of E-3's package. The reviewer is
    told to gate on a committed tip only.
  - **RW-12 (the E-008 deviation): accepted as recorded, no rule change.**
    196 calls, ~330k tokens, finished green with one well-scoped item whose
    design was fixed by RW-4..RW-6. One data point for the E-008 record
    (nyxloom `design-context-lifecycle-experiments.md`), to be added when
    that record is next touched, not a new doctrine.
  - **Follow-up package sent to the implementer (same session, context
    intact):** RG-40 section + index row; the RW-10 paragraph and SPEC
    line; CHANGES `[Unreleased]` unchanged except a one-line pointer to
    RG-40 under RG-36's entry; one selftest run; one commit; return. The
    reviewer (fresh, never a fork, 3-round cap) is dispatched on that tip.

- **2026-09-02 (follow-up package landed at `73e6b061`; reviewer round 1
  dispatched)** — Verified: six commits since main, tree clean, selftest on
  the committed tip `488 passed, 2 skipped` / `diff-coverage OK: 269/269`
  / `lane 'selftest' exit 0` (`selftest-rw910.log`, read separately).
  RG-40 filed with an index row and a measured scope (5 container command
  lanes vs 3 container assay lanes across vbpub's `run-gate.toml` files,
  read with `tomllib`), acceptance criteria including "output still reaches
  the operator unbuffered and in order", NOT implemented. RW-10's claim was
  verified empirically before being written: a host conjunction lane whose
  container sub-lane carries a mismatched inflight record exits 2 through
  the `&&` chain, the sub-lane's refusal names the sub-lane, its container
  and `--fresh`, and the transcript sits in the CONSUMERS paragraph itself;
  SPEC gained `R-39d`; CHANGES `[Unreleased]` gained one pointer line under
  RG-36. Reviewer round 1 dispatched (fresh Opus, never a fork) on
  `73e6b061`: blind diff pass first, RW-1..RW-12 as the yardstick, the
  red-first reproduction, one selftest on the committed tip, one live
  re-attach probe under the host rule (Wave D generation 9 holds the gate
  container at the moment; the prompt says wait, never two), report
  committed as the only file it touches.

- **2026-09-02 (reviewer round 1: NOT ACCEPT — 2 BLOCKER, 7 SHOULD-FIX,
  5 NIT, 6 hollow tests; RW-13..RW-19; fix package dispatched to a FRESH
  implementer)** — Report `be7d94b3` on the branch
  (`run-gate-WAVE-RESUMABLE-REVIEW-round1.md`). Verified green by the
  reviewer independently: red-first reproduced on a detached `main`
  worktree (`assert 2 == 1`), selftest on `73e6b061` `488 passed` /
  `diff-coverage OK 269/269` / `exit 0`, live re-attach probe on a real
  container (SIGKILL survived ≥16 s, no second container, one history entry
  from the container's clock). The findings are accepted as real; every one
  is ruled below. The fix goes to a fresh implementer (E-008: the original
  is past 340k tokens), seeded with the review, these rulings and the wave
  records.
  - **RW-13 (B1 — RG-32's impact and migration): all three halves.** (i)
    CHANGES, REPORT, LOG and the dstdns notification say the PARSED number —
    13 of 29 dstdns lanes refuse at load (the reviewer's loader run, listed
    by name) — and describe the two-round shape a text-grep sweep hid. (ii)
    Migration text: a LANE key found under a pin table is MOVED one level
    up, never deleted; `clean_tree = false` is named as the case that occurs
    today on four dstdns lanes, where deleting it would silently flip them
    to the default `true`. (iii) Code: when an unrecognised pin key is itself
    a legal lane key, the refusal says so in one clause — "`clean_tree` is a
    lane key; it belongs one level up in `[lanes.<n>]`, where it is
    load-bearing — move it, do not delete it". Red-first against the
    current generic message. The four inert `clean_tree = false` lines are
    a live dstdns defect run-gate just found; the notification says so as a
    finding for dstdns to file (a controller-to-peer message, not a run-gate
    change).
  - **RW-14 (B2 — two clients, one lane): owner-liveness in the record;
    an alive owner is FOLLOWED, never hijacked.** The record gains
    `owner_pid`, `owner_start` (the process start time read from
    `/proc/<pid>/stat`, so a reused pid is not mistaken for the owner) and
    `boot_id` (`/proc/sys/kernel/random/boot_id`). On invocation with a
    record whose owner is ALIVE (same boot, pid present, same start time):
    the second client prints `run-gate: following <name> (owner pid N,
    started <t>)`, streams `docker logs -f`, `docker wait`s, exits with the
    container's code, and does NOT `rm` the container, NOT clear the record,
    NOT write history — the owner does all three, exactly once (RW-3 holds
    end to end). `--fresh` while the owner is alive REFUSES (exit 2) naming
    the pid: run-gate never kills another client's run. Owner DEAD (other
    boot, pid gone, or a different start time) → re-attach as owner, the
    behaviour RW-1 already defines. `SPEC.md:895`'s arbitration sentence is
    replaced by this rule; RW-2 is amended to match. The reviewer's
    two-client probe becomes the red-first test: A exits 0 with its true
    result, B follows and exits 0, one `docker run`, one `rm`, one history
    entry. Rejected: a lifetime lock (refusing the second terminal outright
    loses the follow, which is the operator's most common second
    invocation) and deleting the claim (the behaviour, not the sentence, is
    the defect).
  - **RW-15 (S1 — `--since`): dropped outright.** Plain `docker logs -f`
    replays from the first line; `started_at` stays in the record for
    display and duration only. The argv-asserting test (`:6245`) is replaced
    by one that asserts the replayed FIRST line (hollow test 1).
  - **RW-16 (S2 + S3 — identity and the daemon): `{{.Id}}` joins the
    existing `docker inspect -f` format; an id mismatch is GONE-by-name**
    ("a different container now wears this name; run-gate will not touch
    it"), record cleared, fresh run. **"No such object"/"No such container"
    on stderr is the ONLY gone signal**; any other inspect failure is
    infrastructure — exit 3, record UNTOUCHED, no history write — so a
    daemon restart on this host can never orphan a live container or write
    a false `aborted`. Ask 4 answered: distinguish, do not document a loss.
  - **RW-17 (S4 — collected duration): `FinishedAt − StartedAt` from
    `docker inspect` on the collect path**, the record's `started_at` only
    as the fallback when inspect lacks either; the re-attach path keeps
    `now − started`. Ask 5 answered: use `FinishedAt`.
  - **RW-18 (S5 + S6 — what the client says): `--dry-run` computes the
    commit comparison FIRST and names the refusal it would give; the
    re-attach and follow paths print the same `budget` and `stall_timeout`
    disclosure lines the fresh path prints** (facts about the lane, not
    about this client's mounts). Ask 6 answered: RG-40's acceptance criteria
    gain "the source-of-signal line is printed on re-attach and follow too"
    now, so the next wave inherits the rule and not the defect.
  - **RW-19 (S7, N1–N5, hollow tests 2–6): all taken.** S7: the dstdns
    consumer for RG-34 is `scale-admission` (`schema` was fixed at
    `dstdns@65582354`); N1: SPEC says what the poll actually does (stat +
    full read + per-line parse) — an incremental read from a saved offset is
    accepted if the implementer prefers it, the sentence must be true
    either way; N2: `INFLIGHT_SCHEMA` checked on read, a mismatch disclosed
    and treated as no record; N3: the record's `verdict` and `progress` ARE
    READ on re-attach and follow (they are that run's declared artifacts;
    the live config is not the authority for a run already in flight); N4:
    a progress file that vanishes mid-run gets its own sentence and silence
    keeps counting toward `stall_timeout` (a vanished file is silence, not
    "not an R2 lane"); N5: the CONSUMERS transcript verbatim, and the
    wave-prompt path stated as the main checkout's. Hollow tests 2–6
    replaced by behaviour assertions exactly as the review lists them.
  - **Fix implementer dispatched** (fresh Opus): one package in the order
    RW-14 → RW-13 → RW-16 → RW-15 → RW-17 → RW-18 → RW-19, red-first where
    the review's probes already express it, selftest on the committed tip
    only, one live two-client probe under the host rule, records appended
    (LOG/REPORT), no push. Reviewer round 2 on its tip (round cap 3).

- **2026-09-02 (session limit — fix implementer checkpointed at
  `e87007cc`; RW-20..RW-22; NO successor dispatched yet)** — Operator:
  "we approach the session limit, don't start new work, make sure you can
  resume running agents." The fix implementer cut at a green gate: RW-14
  (`73f2bb14`, follow-never-hijack, `R-39e`), RW-13 (`074ae074`, move-not-
  delete clause, impact re-measured at 13 of 29 dstdns lanes, four
  `clean_tree` moves), RW-16 (`3f157a3e`, `{{.Id}}` + "No such object" as
  the only gone signal), coverage tests (`450b3e22`), LOG + BRIEF-1
  (`e87007cc`). Selftest on `450b3e22`: `505 passed, 2 skipped`,
  `diff-coverage OK: 342/342`, `lane 'selftest' exit 0` — QUOTED from the
  implementer's return message; the named log (`selftest-fix-rw16b.log`)
  was NOT found under any scratchpad on this host, so the successor's first
  act is one selftest on the committed tip, read separately, before
  building on it. B2's red-first proven against `be7d94b3` (per the same
  report). **Remaining: RW-15, RW-17, RW-18, RW-19, and the live
  two-client probe** — seams in
  `run-gate-WAVE-RESUMABLE-BRIEF-1.md` on the branch. The successor is a
  FRESH implementer seeded with BRIEF-1 + RW-13..RW-22, dispatched when the
  operator says work may start again; then reviewer round 2.
  - **RW-20 (ask 1 — live owner, container already gone): bounded
    re-poll, then refuse.** Re-read the record and the owner's liveness up
    to three times over about one second before refusing by pid; the race
    is the owner's own `rm -f` → `clear_inflight_record` window, so the
    second read normally finds no record and the run proceeds fresh. A
    refusal that names a finished run is worse than a one-second wait.
  - **RW-21 (ask 2 — the follower's early `docker wait`): confirmed.** One
    extra docker client for the lane's duration is the price of a follower
    that reports the true exit code after the owner's `rm -f`; `R-39e`
    states it as the reason.
  - **RW-22 (ask 3 — an impostor container left running): confirmed as
    shipped; file RG-44** ("`doctor` names a container wearing a run-gate
    lane name that no inflight record owns") for the next run-gate package,
    together with RG-41..43, after the 23.4.0 merge. run-gate never removes
    what it did not start.

- **2026-09-03 (limits reset — fix SUCCESSOR dispatched; E-5 seams
  implementer dispatched in parallel)** — Operator: "limits have reset.
  switch to max parallelism now." Successor (fresh Opus, BRIEF-1 seed,
  RW-13..RW-22) on `e87007cc`: FIRST ACT is one selftest on the committed
  tip read separately (the previous implementer's verdict was quoted, its
  log never found), then RW-15 → RW-20 → RW-17 → RW-18 → RW-19 → R-39e's
  RW-21 sentence, then the live two-client probe (follow + kill-9
  re-attach) under the host rule, then return for reviewer round 2. In
  parallel, on a separate branch `feature/run-gate-buildkite-seams` from
  main: E-5 seams 2 and 4 (`tools/buildkite/pipeline.sh` from `--list`,
  `tools/buildkite/bk-lane.sh` with `--dry-run`, their tests, doc updates
  to `REMOTE-LANES-BUILDKITE.md` only) — that agent touches none of the
  files this wave edits, starts no container, and is folded into CHANGES
  and the backlog (RG-41..44) by the controller after 23.4.0 merges.

- **2026-09-03 (fix SUCCESSOR returned — every ruling landed at `21e6bbea`,
  selftests green, live probe both scenarios PASS; RW-23..RW-26; reviewer
  round 2 dispatched)** — Verified in a separate step: `selftest-succ-0.log`
  on the inherited tip `e87007cc` `505 passed, 2 skipped` /
  `diff-coverage OK: 342/342` / `exit 0` (the independent read the previous
  entry owed), and `selftest-succ-3.log` on `21e6bbea` `538 passed, 2
  skipped` / `diff-coverage OK: 452/452` / `exit 0`; eighteen commits since
  main, tree clean, `__revision__ = 34`. Landed: RW-15 `a8dc5ebc`, RW-20
  `cb6104a4`, RW-17 `86f7f7b4`, RW-18 `ffc64903`, RW-19 `d7e280ce`
  (+ RW-21's `R-39e` sentence; RW-22 confirmed), records `21e6bbea`.
  Red-first proven for RW-15/18/19/20 (seven cases); RW-17's red at the
  seam with an honest note. Live probe under the host rule, transcripts in
  the REPORT: scenario 1 — A exits 0 with its true result, B prints
  `following … (owner pid N, started …)`, ONE `docker run`, ONE `rm`, ONE
  history entry (at `73e6b061` the same shape gave A exit 3); scenario 2 —
  kill -9, re-attach as owner, replay from `FIRST-LINE-AT-START`, container
  clock duration. Two findings beyond the rulings, both accepted: hollow
  test 6 hid a real defect (silence was measured from the first OBSERVED
  event, so an already-frozen container got a fresh window on re-attach;
  now from the watch's construction), and RW-14's stateful fake docker
  interleaved two clients' argv lines (~1 in 15 flake; one `write()` per
  line now). One intermediate gate went red because the implementer edited
  files while it ran — RG-39's own trap, re-measured clean.
  - **RW-23 (ask 1 — RW-18 applied to the follow and `--fresh --dry-run`
    branches): confirmed.** The rule is "the dry run names the real
    outcome on every branch"; both branches were this wave's own.
  - **RW-24 (ask 2 — silence measured from the watch's construction):
    confirmed.** A container already frozen when a client re-attaches has
    been silent at least that long; SPEC R-40 says so in one sentence.
  - **RW-25 (ask 3 — an unknown-schema inflight record): REFUSE, do not
    degrade.** Degrade-and-overwrite lets an older client destroy a newer
    client's record — the exact loss RG-35 exists to end. Exit 2 naming
    both schema numbers and the remedy (upgrade run-gate; or `--fresh`
    when the record's container name is readable; else the record path to
    delete). Lands in the post-round-2 fix package (or as one commit
    verified at merge if round 2 has no findings).
  - **RW-26 (ask 4 — RG-34's box shape): close RG-34 as FIXED** (run-gate's
    half is the `doctor` WARN, shipped); the `scale-admission` hit is the
    dstdns notification's business and a dstdns filing, not an unticked
    box run-gate can never tick. Same package as RW-25.
  - **Reviewer round 2 dispatched** (the round-1 reviewer's session, cap
    3) on `21e6bbea`: every round-1 finding re-reproduced, new-defect hunt
    on the owner-liveness edges, one selftest, report as its only file.

- **2026-09-03 (reviewer round 2: NOT ACCEPT — 1 BLOCKER, 4 SHOULD-FIX,
  2 NIT, zero round-1 regressions; RW-27..RW-31; fix package 2 to a FRESH
  implementer; round 3 = verification only)** — Report `4a7a490b`.
  Selftest on `21e6bbea` re-run by the reviewer: `538 passed` /
  `diff-coverage OK: 452/452` / `exit 0`. All fourteen round-1 findings
  re-reproduced FIXED (B2's two-client probe: A exit 0 true result, B
  follows, one run/one rm/one history entry; B1's new refusal sentence
  verbatim; S4's duration 3600.0). New findings, all accepted:
  - **RW-27 (G1, BLOCKER — RW-24 as implemented kills a slow-starting lane
    on the poll that proves it alive): seed the silence clock from the
    FILE.** On the first observation, age = `wall_now − mtime` (the mtime
    `_newest()` already reads), so a re-attached frozen file stalls at
    once (RW-24's case) and a fresh lane whose first candidate arrives
    after a long startup (dstdns `sql-mutation`: provision, baseline,
    mutant generation before candidate #1) gets its full window. R-40's
    sentence gains its second half: silence is measured from the last time
    the FILE moved, which for a re-attach is before this client existed.
    Red-first: the reviewer's driven-clock probe (`candidate 1/172` printed
    and stalled by the same `poll()`).
  - **RW-28 (G2 — a follower outliving its owner leaves the orphan): promote.**
    After `docker wait` returns, re-read the record and the owner's
    liveness; if the owner is gone, the follower does the three duties
    (`rm -f`, clear, ONE history entry with the exit code it holds) and
    discloses by name ("the owning client (pid N) is gone; this client is
    finishing its cleanup"). "One run, one rm, one history entry" stays
    true in the one case that broke it.
  - **RW-29 (G3 — owner identity not PID-namespace-safe): add the conjunct.**
    The PID namespace inode (`os.stat("/proc/self/ns/pid").st_ino`) joins
    the record and `live_owner_pid`; a record from ANOTHER namespace is
    "liveness unknown" and is treated as ALIVE — follow, or refuse on
    `--fresh` — never as dead. This host bind-mounts the same worktrees
    into several devcontainers; the hazard is real here.
  - **RW-30 (G4 — the dstdns count is already stale, 18 of 35 today):
    measured-on-date plus the one-liner.** CHANGES/SPEC/REPORT/notification
    say "N of M dstdns lanes as measured on <date> with `<command>`" and
    carry the re-measure command; the number is re-measured once more at
    merge. The four `clean_tree` moves are unchanged.
  - **RW-31 (G5 — the LOG records an N5 fix that did not land): land it**
    (the wave-prompt path stated as the main checkout's) and correct the
    LOG entry rather than rewrite it (append a correction line).
  - **Plus RW-25 and RW-26** (refuse an unknown-schema record; close RG-34
    as FIXED with the `scale-admission` hit in the notification only) —
    verified absent by the reviewer, land in this package.
  - **Round 3 (ask 5): verification-only** of RW-25..RW-31 on the committed
    tip by the same reviewer — no third full pass. Fix package 2 goes to a
    FRESH implementer (the successor is past 300k tokens).

- **2026-09-03 (fix package 2 returned — all seven rulings + both round-2
  nits landed at `661fde05`, gate GREEN; RW-32..RW-36; one addendum commit
  before round 3)** — Verified in a separate step from
  `selftest-pkg2-4.log`: `553 passed, 2 skipped` / `diff-coverage OK:
  494/494 changed executable lines covered (100.0% ≥ 100.0% floor)` /
  `lane 'selftest' exit 0`; tree clean, `__revision__` still 34. Landed:
  RW-27 `43d66ba8`, RW-29 `d16d9380`, RW-28 `7fd45793`, RW-25 `3af55353`,
  both round-2 nits `713887fc`, RW-30/RW-31/RW-26 `2cb91b4e`, `pid_ns_inode`
  coverage `f4a46459`, records `661fde05`. Red-first proven at the seam for
  RW-27 (the reviewer's self-refuting `candidate 1/172` + `has not advanced
  for 1230s`), RW-29 (two-step: a foreign-namespace record was
  re-attached, then `--fresh` exited 0 having `rm -f`'d another client's
  container), RW-28 (empty promotion disclosure; the now-false "the owning
  client preserves the evidence"), RW-25 (exit 0 with its own container
  started over the unreadable record). No live docker probe: the package
  adds no new docker argv shape and RW-28's promotion is fully expressible
  against the stateful fake docker. Five decision asks, ruled:
  - **RW-32 (ask 1 — RW-28's promotion on a failing container destroys the
    evidence `rm -f` would remove): CONFIRMED, already implemented, no
    addendum needed.** [Correction, same entry: the report's phrasing
    ("this adds…") was read as a not-yet-applied proposal; `promote_follower`
    at `run-gate.py:3444` already calls `save_container_logs` before the
    `rm -f` on a non-zero exit, and `follow_container:3491-3502` already
    prints the promoted-vs-not-promoted disclosure with the evidence path
    or its absence named. The commit is `7fd45793` (RW-28) itself — the
    implementer built the fuller behaviour in the first pass and asked
    whether the deviation beyond RW-28's literal three duties was wanted.
    It is. Nothing further to land; round 3 proceeds on `661fde05` as-is.]
  - **RW-33 (ask 2 — RW-29's foreign-namespace record still names a pid
    meaningless in this namespace): confirmed as implemented.** A second
    return channel so all six message sites could say "an unverifiable pid
    N" was considered and is too large for what it buys; the boundary
    disclosure line immediately before the existing messages is enough —
    a reader who follows the log sees the caveat once, in order.
  - **RW-34 (ask 3 — RW-25's `--fresh` degrade reads `container` and
    `started_at` from an unknown-schema record): confirmed as
    implemented.** Two fields is the minimum a human-directed `--fresh`
    needs to name what it is about to remove; narrowing further would make
    the remedy line in RW-25's refusal message untrue.
  - **RW-35 (ask 4 — an inflight record with no `schema` key is corrupt,
    not foreign): confirmed as implemented.** Rev 34 is the first revision
    that writes the file at all, so an absent key has no innocent
    explanation (no prior revision ever omitted it) — treating it as
    corrupt (no record returned) rather than foreign (refuse) is the
    correct read of RW-25's intent.
  - **RW-36 (ask 5 — commit trailers changed mid-package, Fable 5.1 through
    `3af55353` then Sonnet 5 from `713887fc`): no history rewrite.** The
    harness re-issued its attribution instruction mid-flight; both
    trailers are an honest record of when each commit was made under which
    instruction. The wave's merge commit carries the CURRENT attribution
    (Sonnet 5, this session's `Claude-Session` URL); nothing on the branch
    is rewritten.

- **2026-09-03 (round 3: NOT ACCEPT — one real finding, everything else
  driven to a genuine observation and PASS; RW-37; fix-and-reverify, no
  round 4)** — Report `a5b2834a` on the branch
  (`run-gate-WAVE-RESUMABLE-REVIEW-round3.md`). Six of seven checklist
  items independently driven (not just read) and PASS: RW-27 with the
  reviewer's own `FakeClock`, RW-28's promotion plus its RW-32 evidence
  deviation across 17 tests, RW-29's foreign-namespace path driven live,
  RW-25's core refusal, RW-30/31/26 re-measured directly against
  `/workspaces/dstdns/run-gate.toml` (18 of 35, matching exactly), both
  round-2 nits, and a fresh selftest on `661fde05` (host rule observed:
  waited for a competing gate container) — `553 passed, 2 skipped` /
  `diff-coverage OK: 494/494` / `exit 0`, matching package 2's own numbers
  exactly.

  **RW-37 (RW-35 was WRONG — confirmed a claim that is not true): fix,
  narrowly, no new full round.** `load_inflight_record`
  (`run-gate.py:1216-1218`) only refuses on `schema is not None and schema
  != INFLIGHT_SCHEMA` — an ABSENT `schema` key skips that branch entirely
  and, if `container` is present, the record is returned as if valid. The
  reviewer found the actual failure is worse than the two alternatives
  RW-35 weighed: a schema-absent record whose `commit` matches HEAD
  reaches `resolve_inflight()` → `adopt_inflight_start()`
  (`run-gate.py:1556`), which does a bare `pending["started_at"]`
  subscript and raises an unhandled `KeyError` if that field is also
  missing. SPEC.md:953 states the same false claim ("A record carrying no
  `schema` key at all is not a versioned record but a corrupt one") — the
  SPEC is right about intent, the CODE never implemented it; `--fresh` is
  unaffected (it already reads via `.get()`). Independently reproduced by
  the controller before ruling: `load_inflight_record` at
  `run-gate.py:1216` confirmed to return the dict unchanged when `schema`
  is absent; `adopt_inflight_start` confirmed to use a bare subscript, not
  `.get()`.

  **Fix, one line plus a regression test:** in `load_inflight_record`, an
  absent `schema` key returns `None` (corrupt, matching SPEC.md:953 and
  RW-35's original intent) BEFORE the mismatch branch runs — `schema is
  None` is its own case, not folded into `schema != INFLIGHT_SCHEMA`. One
  red-first test: a record with `container` set, `commit` matching HEAD,
  no `schema` key, and no `started_at` — must not reach
  `adopt_inflight_start` at all (returns `None`, so `resolve_inflight`
  treats it as no record and runs fresh). This is round 3's own review
  target, dispatched as a small, tightly-scoped fix — not a fourth full
  adversarial round (the round-cap 3 is exhausted, and the reviewer itself
  recommended fast fix-and-reverify over reopening the wave). The
  controller verifies the diff and the selftest directly; no new reviewer
  round is dispatched for a single-conditional, mechanically-checkable
  fix with an exact reproduction already in hand.

- **2026-09-03 (SEPARATE ITEM, not this wave — a NEW backlog finding on
  main, dispatched to implementation)** — Operator: "i think run-gate has
  new backlog, you can send to implement." `KNOWN_ISSUES_TODO_BACKLOG.md`
  gained **RG-39 on `main`** (commit `1f312ab1`, 2026-09-03, filed from
  dstdns D-321/D-339/D-321-correction): no internal mutual exclusion
  around `docker exec`/`docker run` into a resolved container, so every
  consumer must remember an external `flock` or two lanes racing the same
  container silently contaminate each other's evidence. Already annotated
  buildable by the ciu v8 design review (`ccbc02bf`, SPEC-V8 draft.5 §4.11
  N22) with three refinements — (1) exec mode only, (2) lock AFTER
  `acquire_shared_locks()`'s shared-infra locks in the SAME fixed order,
  released in the same `finally`, `/tmp/run-gate-exec-<container>.lock`
  with RG-20's 0600+O_NOFOLLOW discipline, blocking `LOCK_EX`, dry runs
  plan-never-block, (3) v8 alignment (keying on a Realization's stack
  directory once RG-37 exists) — **explicitly deferred, RG-37 doesn't
  exist yet; only (1)+(2) are in scope.** Dispatched to a fresh Opus
  implementer on a NEW branch `fix/run-gate-exec-mutex-rg39`, worktree
  `.worktrees/run-gate-exec-mutex-rg39`, off `origin/main` tip `23fe2c98`
  — independent of both in-flight waves, touches neither's files.
  **ID-COLLISION FOUND, noted for the run-gate wave's OWN merge step (not
  acted on now — that worktree is mid-fix for RW-37, do not touch it
  concurrently):** the wave's OWN branch (`feature/run-gate-wave-resumable`)
  independently allocated `RG-39` (coverage_gate.py dirty-tree line-number
  offset, `d4e8e137`) and `RG-40` (container command-lane liveness /
  RW-9's E-3 candidate, `73e6b061`) — from a point where main only had up
  to RG-38. Main has since taken the REAL `RG-39` for the mutex finding
  above. **At the run-gate wave's merge (after this session's RW-37 fix
  lands), its two rows must be renumbered before merging: coverage_gate
  → RG-40, command-lane-liveness → RG-41** (main's next free number after
  its own real RG-39), and the wave's already-planned NEW filings shift
  from RG-41..46 to **RG-42..47**. Any narrative use of "RG-39" in that
  wave's own LOG/REPORT prose (e.g. "RG-39's own trap" for the
  never-edit-during-a-selftest rule) refers to the coverage_gate finding
  and should read as RG-40 after renumbering — cosmetic, not a substance
  change, and low priority relative to actually landing the number
  correctly in `KNOWN_ISSUES_TODO_BACKLOG.md`, `CHANGES.md` and
  `SPEC.md`.
  **Rev-number note for the same merge:** this new exec-mutex branch
  bumps `__revision__` from 33 independently of the wave's own bump to
  34 — whichever of the two merges to main SECOND must renumber its own
  bump to the next free integer at merge time, not before.

- **2026-09-03 (RG-39 exec-mutex implementer returned, gate GREEN on
  `fefc6c19`; one review round dispatched before merge)** — Verified
  independently: `495 passed, 2 skipped` / `diff-coverage OK: 25/25` /
  `exit 0` (`rg39-selftest-final.log`, read separately); scoped diff
  against the branch's own fork point `23fe2c98` touches exactly five
  files (`run-gate.py` +100/-10, `tests/test_run_gate.py` +225, `SPEC.md`
  +26, `CHANGES.md` +13, `KNOWN_ISSUES_TODO_BACKLOG.md`'s RG-39 row only);
  tree clean. (The much larger diff against a freshly-fetched
  `origin/main` is a peer session's unrelated `ciu` v8 design push —
  `c3730525`, draft.6 — landed on main after this branch forked; benign,
  no file overlap, not a concern.)
  - Built: `_open_lockfile()` factoring RG-20's 0600+`O_NOFOLLOW`
    open-flags discipline so RG-39's lock is a sibling, not a near-copy;
    `acquire_exec_lock()` (`/tmp/run-gate-exec-<container>.lock`, blocking
    `LOCK_EX`, contention line naming the container and the lock path,
    `--dry-run` plans without opening or blocking); `resolve_container_name()`
    now called ONCE in `main()`'s dispatch rather than inside
    `run_exec_lane()`, threaded through as parameters so the lock key and
    the actual `docker exec` target can never independently drift; the
    lock acquired strictly AFTER `acquire_shared_locks()` and released in
    the SAME `finally` (fixed global order, no ABBA against RG-20),
    `flush_run_record` still outside the held window (RG-27 unaffected).
    Six new tests (`TestExecModeMutex`): same-container serialization
    (thread-raced), isolated containers never contend, `--dry-run` never
    blocks, the lock releases on the lane's own exception (`finally`
    path), a direct ordering assertion (shared-infra before exec-lock, via
    monkeypatched call tracking), and the `OSError` branch. Red-first
    proven with a scoped `git stash push -- run-gate.py` (3/5 new tests
    failed for the expected reasons, restored via `stash pop`). One
    self-caught defect during that red-first pass, fixed in-place and
    disclosed rather than hidden: a leaked lock fd on the FIRST red run's
    own assertion-failure path self-deadlocked the NEXT test via
    `flock()`'s per-open-file-description semantics — the same latent gap
    pre-exists, untouched, in this file's own EXISTING RG-20 lock tests;
    correctly left out of scope rather than opportunistically fixed
    elsewhere. Refinement (3) (v8 stack-directory keying) correctly NOT
    built — RG-37 doesn't exist yet, as instructed.
  - **Merge sequencing decided:** the run-gate WAVE (`feature/run-gate-
    wave-resumable`, closer to done — RW-37 finishing now, three review
    rounds already spent) merges to main FIRST and keeps `__revision__ =
    34`. This exec-mutex branch merges SECOND, after its own review
    round below — at that point its `__revision__` bump renumbers 34→35,
    and its rev-history docstring line is edited to read "rev 35" and
    name rev 34 (the wave) as the immediately preceding entry. Not done
    now — the branch is otherwise complete and this is a one-line
    mechanical edit at actual merge time, verified by a fresh selftest
    then, not before.
  - **One adversarial review round dispatched** (fresh Opus, never a
    fork) on this branch's own tip — new locking/concurrency code around
    a shared host resource warrants the same scrutiny RG-20's own
    admission locks got, even though the implementer's self-testing
    (including the self-caught deadlock finding above) was unusually
    thorough. Own detached worktree/clone for its probes; told the
    merge-sequencing decision above so it doesn't flag the pending
    revision renumbering as a defect.

- **2026-09-03 (RG-39 review round 1: ACCEPT; MERGE-READY, waiting on the
  wave to land first per the sequencing decision)** — Report `467b2358`
  (`ad2b0e2d` a same-round encoding fix, three stray NUL bytes from the
  report's own TOML-escape-syntax prose that made git misclassify the
  file as binary — content/verdict unchanged). Verified independently:
  `495 passed, 2 skipped` / `diff-coverage OK: 25/25` / `exit 0`
  (matches the implementer's own numbers exactly); tree clean. Six push
  points all driven to real observations, not just read: lock ORDERING
  (grepped every caller of both lock functions — exactly one each, both
  in `main()`, order textually enforced, no violation path exists);
  STALE LOCK FILES (grepped for any `unlink`/`os.remove` near a `.lock`
  path — none exist, so no TOCTOU is possible); DRY-RUN (confirmed by
  code reading that the `if dry_run` branch returns before the file is
  ever opened); the SIGNATURE-CHANGE REFACTOR (confirmed `run_exec_lane`
  has exactly one caller, correctly threaded — no stale second call site
  silently bypassing the mutex); RED-FIRST (independently re-swapped in
  the pre-fix code with the new tests kept: 4/6 failed as expected, 2
  passed trivially for legitimate reasons — no hollow tests); the gate
  itself, run fresh in an isolated worktree.
  - **MINOR (not a blocker): `acquire_exec_lock()`'s `except OSError`
    doesn't catch every exception `os.open()` can raise for a pathological
    lock-key character** — a `container_name` containing a NUL byte
    (constructible via a project's own `run-gate.toml`, TOML's control-
    character escape, not rejected by the existing non-empty-string
    load-time check) makes `os.open()` raise `ValueError`, producing a raw
    traceback instead of the documented clean exit-3 `GateError` contract.
    Reproduced end-to-end through the real CLI. Slash, `..`-traversal and
    overlong names were separately tested and are safe. **Confirmed
    inherited unchanged from RG-20's own `acquire_shared_locks`** — not
    introduced by this diff, so it does not block merge. **Filed for the
    next backlog batch** (folded into the RG-42..47 filings already
    planned for after the wave merges — becomes the 7th item in that
    batch, exact number confirmed at filing time once the wave's own
    renumbering lands): a shared charset/control-character validation at
    config-load time for `container_name` and `resources.shared` names
    would close this for BOTH `acquire_exec_lock` and
    `acquire_shared_locks` at once, rather than patching each `except`
    clause separately.
  - **INFORMATIONAL, correctly out of scope, no action:** `build_env_
    probe_argv()` (RG-25/RG-26's environment-inventory probe, unchanged
    by this diff) also `docker exec`s into the same resolved container,
    unsynchronized by the new mutex — traced as a wholly separate,
    pre-existing mechanism (never lock-covered before this branch either,
    RG-39's own scope is the judged lane's exec-and-evidence window, not
    a probe whose own docstring says "a probe's result is never a
    verdict"). Worth knowing if a future RG-37/v8 pass revisits the
    exec-mode contract; not a defect, not filed.
  - **MERGE-READY.** Per the sequencing decision above: this branch waits
    for `feature/run-gate-wave-resumable` to merge first (keeping
    `__revision__ = 34`), then merges second with its own bump renumbered
    34→35, verified by one more fresh selftest at that point before
    calling it done.
