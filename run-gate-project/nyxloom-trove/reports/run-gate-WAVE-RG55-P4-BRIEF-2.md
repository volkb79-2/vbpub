# run-gate-WAVE-RG55-P4 — BRIEF-2 (checkpoint cut, E-008, session 2)

Checkpoint reason: coordinator-issued checkpoint ("far past the ~60-call
ARM") received mid-session, right as this session was about to (re)launch
the real `selftest` gate. Cutting HERE, at a clean, fully-committed tip —
C4 and C5 are both DONE and committed; nothing is half-built. The
coordinator's own instruction: do not wait for memory pressure, do not
launch the selftest gate — checkpoint now instead.

## State

Tip: **`5b80c024`** on branch `rg55-followups-run-gate`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-run-gate`. Working tree
CLEAN (`git status --porcelain` empty, confirmed at cut time). Commits
since this package's own BRIEF-1 checkpoint tip (`e698835f`, session 1's
cut after C1-C3):

1. `c37b6e94` — **C4 (RG-57)**: bare-host lanes are profiled. Full design
   from BRIEF-1, both sub-paths (daemon via self container id, scope
   ALWAYS `container-shared`; daemon-absent via `resource.getrusage
   (RUSAGE_CHILDREN)`, `method: "rusage"`, NEVER a `BasicSampler`).
   R-36h containment resolved a genuine internal contradiction in
   BRIEF-1 itself (its own prose said "must be wrapped" for the
   `getrusage` calls; its own pseudocode showed them unguarded) by
   guarding both `getrusage` calls individually rather than copying the
   literal pseudocode — documented as a decision-ask in the LOG under
   Commit 4's own "R-36h containment" section.
2. `0c782601` — **merge**: `rg55-run-gate-client` (tip `647a2cc6` at
   merge time) into this branch, per a live coordinator instruction
   received mid-session (see "Live coordinator message" in the LOG,
   right after Commit 4's entry) — P2's own close-out work
   (RW-38/39/40, a P8 merge, P2's T6 assay-r2 log entry) folded in
   before C5's gates. Stashed this session's own uncommitted C5 WIP
   first (`git stash push -u`), merged clean (zero conflicts — only
   file P2 touched in `run-gate-project/` since the `186461de` fork
   point was P2's own LOG), then `git stash pop` — restored clean.
   **Do NOT bare `git stash`/`git stash pop` again** — a later
   environment note (received after this merge, thankfully with no
   incident) says the stash stack is SHARED across worktrees/sessions;
   use a WIP commit instead if you need to set work aside, or `git
   stash push -u -m "<unique-tag>"` + capture the SHA + `apply <sha>`
   (never `pop`) if you must stash.
3. `5b80c024` — **C5 (RG-61)**: the eight-item documentation sweep +
   RW-28 config + `__revision__ = 42`. See "What C5 delivered" below —
   everything EXCEPT the live `footprint --write` transcript (item 5)
   landed here; that one item is explicitly deferred (see below, it is
   a real structural dependency, not an oversight).

Each commit's own message + the LOG (`run-gate-WAVE-RG55-P4-LOG.md`) carry
full rationale. Read the LOG's "Live coordinator message" section
(between Commit 4 and Commit 5) before touching anything else — it has
two load-bearing findings a successor must not re-derive:

- **The `selftest` lane's own `tools/coverage_gate.py` invocation
  hardcodes `--base main`** (a literal string in `run-gate.toml`'s
  `[lanes.selftest]` argv, NOT a `{base}` token) and does NOT delegate
  (R-35: a non-delegating lane REFUSES a `--base` flag passed to
  `./run-gate.py` itself). `git merge-base main HEAD` is `3b75e1df` — the
  ROOT of the whole RG-55 wave — NOT `186461de`/`rg55-run-gate-client`,
  so `selftest`'s own coverage_gate.py will judge the FULL P0+P1+P2+P4
  diff (~2825 changed lines in `run-gate.py` alone as of this cut), not
  just this package's own increment, and there is NO flag that changes
  this. The handoff/BRIEF-1's own "vs `--base rg55-run-gate-client`"
  phrasing cannot be a literal CLI instruction given this — read it as
  describing the INTENDED judged scope, not an invocation `selftest`
  supports. **Run `./run-gate.py selftest` with NO `--base` flag** (one
  would be refused) and report the verdict for exactly the (wave-wide)
  diff it actually judges. If it comes back RED, identify whether the
  failing lines are THIS package's own (C1-C4) or pre-existing
  (P0/P1/P2) before doing anything else — a pre-existing gap in another
  package's already-closed work is a finding to report, not something to
  silently patch from inside this worktree.
- **`budget_per_candidate = "900s"`** landed under `assay.toml`'s
  `[lanes.r2.judge.mutation]` table (confirmed present, confirmed
  `tomllib`-parseable, confirmed alongside `jobs`/`max_mutants` in the
  SAME table) — a coordinator message referred to this colloquially as
  `[lanes.assay-r2.judge.mutation]`, but `assay-r2` is `run-gate.toml`'s
  OWN lane name (`[lanes.assay-r2]` → `assay_lane = "r2"`); the actual
  TOML table inside `assay.toml` (a separate namespace) is `[lanes.r2]`.
  Already correctly placed — do not "fix" this into a different table
  name.

## Host load (STILL BINDING — re-check before any command)

At cut time: PID **2415767 is GONE** (P2's FIRST `assay-r2` attempt
exited — it hit its own 4h lane budget). But a **SECOND** P2 attempt is
now running: bare-host pid **1141617** (`python3 ./run-gate.py --base
main assay-r2`, confirmed alive at cut time) — this occupies
run-gate-project's own shared `r2` lane. Also still alive: container
`run-gate-vbpub-r2-680904-1789228700` (`tester-unified:local`, a
DIFFERENT project's mutation run — cgroup-profiler's own P1 package, a
separate worktree, unrelated to run-gate-project but still counts toward
the `≤ 2 gate containers estate-wide` cap).

Per controller ruling **RW-39** (relaxes the ORIGINAL handoff's stricter
sequencing): bare-host, NON-mutation lanes may run now even while other
mutation runs are live elsewhere on the host — one lane at a time, `nice
-n 19 ionice -c 3`, launched only while `cat /proc/pressure/memory`'s
`full avg10` < 5 (it was hovering around 5-10 at cut time, spiking above
the threshold right before this checkpoint — re-check fresh, do not trust
this brief's own numbers).

Re-verify before anything else:
```
kill -0 1141617 2>&1 && echo "P2's SECOND assay-r2 ALIVE" || echo "P2's r2 GONE"
docker ps --no-trunc --filter ancestor=tester-unified:local
cat /proc/pressure/memory
pgrep -af 'assay-r2|assay.cli run r2'
```

**`assay-r2` (mutation) for THIS package stays OFF LIMITS** until the
coordinator explicitly signals P2's run-gate-project `r2` lane is clear —
do not start it on your own judgment even if `pgrep`/`docker ps` come back
empty; the coordinator asked to be the one who says "r2 may start" (see
the LOG's "Live coordinator message" section, item 4).

## What C5 delivered (all in commit `5b80c024`) — RG-61's eight items

1. ✅ New SPEC `R-30c` (doctor's profiler check, RG-55/C7 — had no rule id
   before). Both stale `R-44` cross-references to it fixed.
2. ✅ SPEC `R-30`'s status-line count: "all four" → "all five" (`[INFO]`).
3. ✅ The RG-51 narrative's stale `0/0 -> 100%` text (two spots) corrected
   to describe RW-5's actual SKIPPED-verdict design.
4. ✅ `CONSUMERS.md` gained full `[profile]`/`[footprint]` schema blocks
   (matching `[history]`'s own style) + `RUN_GATE_PROFILE`'s `on`-override
   and by-name-refusal behavior (previously undocumented there).
5. ⚠️ **NOT YET DONE — structural dependency, not an oversight.**
   `CONSUMERS.md`'s `footprint --write` transcript is STILL the
   pre-existing FABRICATED one. It cannot be replaced until AFTER this
   commit lands AND a real, CLEAN-tree, profiled `selftest` PASS exists in
   this project's own `.run-gate/history.json` — `footprint --write`
   distills from `history`, and a DIRTY-tree run is EXCLUDED from
   `history` (`latest` only), so `--allow-dirty` cannot substitute; the
   tree had to be clean, which meant C5 itself had to be committed FIRST.
   **Exact next steps**: run `./run-gate.py selftest` for real (see Gates
   below) → on a PASS, run `./run-gate.py footprint --write` → capture its
   REAL stdout table verbatim → replace `CONSUMERS.md`'s "### The
   footprint manifest" section's fenced transcript with it (byte-for-byte,
   per the backlog entry's own instruction: "fix the column layout to
   match `print_footprint_report`'s REAL output... do not guess") → one
   small, clearly-labeled follow-up commit ("RG-61 item 5: real footprint
   transcript, captured from a live run on `<tip hash>`"). This is the
   SINGLE thing standing between this package and RG-61 being 100% closed.
6. ✅ `[Unreleased]`'s stale header comment fixed; every entry under it
   names its own RG id + the rev-42 bump.
7. ✅ `usage()` gained `RUN_GATE_PROC_ROOT`; stale `R-43g`/`R-43h`
   lane-schema cross-reference fixed; the "both had shipped in code" claim
   corrected (`resources.cpus` is new this rev, not a backfill).
8. ✅ This package's own five FIXED backlog entries (RG-57/58/59/60/61)
   got commit hashes. The entry's own "physician heal thyself" audit was
   done: TWO real test-count mismatches were found in THIS package's own
   LOG and corrected (`TestBareHostStallTimeoutWarning`: claimed 4,
   actually 3; `TestBareHostProfilingWiring`: claimed 9, actually 7).

Also landed in the SAME commit (C4-deferred docs + RW-28, all per
BRIEF-1's own C5 checklist):
- ✅ SPEC `R-43b`'s stale "categorically unprofiled" line corrected.
- ✅ New SPEC `R-43i` (the full bare-host daemon/rusage sub-rule).
- ✅ `R-36j`/`R-43` top-level text: `method` gains `"rusage"` as a third
  value.
- ✅ `R-44a`: documents the new footprint `source` key.
- ✅ `LANE-AUTHORING.md`: resources section notes bare-host lanes are now
  profiled (rusage-maxrss caveat); mutation-lane paragraph gets RW-28's
  mandatory `budget_per_candidate` sentence.
- ✅ RW-28: `assay.toml`'s `[lanes.r2.judge.mutation]` gets
  `budget_per_candidate = "900s"`.
- ✅ `__revision__ = 42`, with a matching rev-42 inline-changelog entry at
  the top of `run-gate.py` in the file's own established style.

**Nothing else from C5 is open** except item 5's live transcript capture
above.

## Gates (run in order, per the handoff — NONE have run yet this session)

The FULL sequence, per the handoff and the coordinator's live message:

1. **`nice -n 19 ionice -c 3 ./run-gate.py selftest`** (bare-host; NO
   `--base` flag — see "State" above for why one would be refused).
   Read the verdict in a SEPARATE step, never a pipe tail. This ALSO
   produces the real, clean-tree profiled PASS that item 5's transcript
   needs — do items together: selftest PASS → `footprint --write` →
   patch `CONSUMERS.md` → one small follow-up commit — before moving on
   to r1/r3, so the transcript-capture step is not forgotten.
2. **`./run-gate.py assay-r1`** (bare-host).
3. **`./run-gate.py assay-r3`** (bare-host, the canary lane).
4. **`assay-r2` — DO NOT RUN.** Wait for the coordinator's explicit
   signal that run-gate-project's own `r2` lane is clear (P2's second
   attempt, pid noted above, must finish first — AND `pgrep`/`docker ps`
   must show no other mutation run anywhere per the original handoff's
   own rule, on top of the coordinator's explicit go-ahead).

Each gate at most once. Every mutation survivor (when r2 finally runs): a
test that kills it, or a written equivalent-mutant justification in the
REPORT (RW-20/RW-22). Launch r2 UNTRACKED when the time comes (`nohup
./run-gate.py assay-r2 > <scratchpad>/p4-r2.log 2>&1 & disown`) with a
cheap TRACKED `until ! kill -0 <pid>; do sleep 60; done` watcher (RW-26).

After selftest/r1/r3 all have verdicts (and the footprint transcript is
captured + committed): **return to the coordinator** with the tip hash
and the verdict lines — do NOT wait for r2 unless the coordinator has
explicitly cleared it by then.

## Decision asks (recorded, not blocking — RW-9)

1. **`selftest`'s hardcoded `--base main`** (State section above) — taken
   as: run it as-is, report the real (wave-wide) verdict, investigate any
   RED honestly before assuming it is this package's fault. Not yet
   exercised this session (checkpoint landed before the gate ran).
2. **R-36h containment's internal contradiction in BRIEF-1** (C4's own
   LOG entry, Commit 4, "R-36h containment") — resolved by guarding both
   `getrusage` calls individually; already shipped in `c37b6e94`, restated
   here only so a reviewer questioning it finds the rationale fast.
3. **RG-61 item 5's transcript** — deferred for the structural reason
   above, NOT silently dropped; the LOG's "Live coordinator message"
   section and this brief both record it as open.

## REPORT status

**Not yet written.** The handoff requires a REPORT with per-deliverable
evidence, mutation-check transcripts, the live `footprint --write`
transcript, a docs disposition table, a survivor table, and an E-002
telemetry section. None of that exists yet as a separate file — the LOG
carries equivalent detail per-commit so far, but the REPORT itself still
needs to be assembled (after the gates finish, per the handoff's own
Records section: "REPORT: per-deliverable evidence... Claim only what you
ran").

## Self-authored retention prompt (paste into the successor's context)

> KEEP: this BRIEF-2 file in full (it is the plan); tip hash `5b80c024`;
> the `--base main` finding and why `selftest` must run with NO `--base`
> flag (a load-bearing, non-obvious fact — do not re-derive, do not pass
> `--base` and assume the refusal is a bug); the `budget_per_candidate`
> table-name clarification (`[lanes.r2...]` in `assay.toml`, not
> `[lanes.assay-r2...]`); the "r2 stays off limits until the coordinator
> says so" rule; the shared-stash-stack warning (no bare `git stash`/`pop`
> — use a WIP commit or `apply <sha>` instead); RG-61 item 5's exact
> remaining steps (selftest PASS → `footprint --write` → patch
> `CONSUMERS.md` → one small follow-up commit, in that order, before
> moving to r1/r3 so it is not forgotten); the full "What C5 delivered"
> checklist (items 1-8, all done except 5).
> DROP: the tool-by-tool trace of how C4/C5 were implemented (already IN
> the commits and this package's own LOG file, which stays the detailed
> record — this BRIEF is the distilled next-steps map, not a second copy
> of the LOG); the full orientation narrative from session 1's own BRIEF-1
> (already absorbed, already superseded by "what remains" above).

## Telemetry (E-002, for the REPORT this package still owes)

This session (session 2, the fresh successor picking up BRIEF-1) spent
its budget on: re-orientation (~15 calls: BRIEF-1, HANDOFF, LOG, REPORT
check, controller log RW-26/27/28, host-load re-check, P2-merge check),
implementing + testing C4 (~50 calls: reading the profiling helper
functions, writing `resolve_self_container_id`/`start_bare_host_
profiling`/`finish_bare_host_profiling`, rewriting `run_bare_host_lane`,
fixing a disclosure-gating bug the test suite itself caught, writing 9
tests across `TestBareHostProfilingWiring` + a new `TestFootprintVerbCLI`
acceptance test, five targeted pytest sweeps), implementing C5 (~35
calls: the eight backlog items across five files, the RW-28 config edit,
the revision bump, the backlog audit that found two real test-count
mismatches), then the merge + coordinator back-and-forth (~15 calls:
investigating the `--base main` finding, stashing/merging/popping,
committing C5, discovering the dirty-tree refusal, and this checkpoint).
Total this session: comfortably past 100 tool calls by the time the
checkpoint landed — the coordinator's "far past ~60" was accurate. Nothing
was missing from BRIEF-1 that blocked progress; the two genuine NEW
findings this session surfaced on its own (the `--base main` hardcoding,
and R-36h's internal pseudocode-vs-prose contradiction) are both now
recorded for the next reader rather than left to be rediscovered.
