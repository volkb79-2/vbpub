# cgprofile-P6-FOLLOWUPS — BRIEF for session 11 (fresh, no memory of session 10)

Session 10 (fresh Sonnet, dispatched ~20:30Z) merged P1's fix, proved it
live on this tree, launched the r2 lane, and is now being wound down by
the controller (operator instruction: no new work; every agent
checkpoints to files). **r2 is still running unattended — do not touch
it, do not wait for it in this session's own lifetime; just check its
state per "Exact next steps" below and act on what you find.**

## State

- Worktree `/workspaces/vbpub/.worktrees/rg55-followups-cgprofile`,
  branch `rg55-followups-cgprofile`. Do NOT create another.
- **Tip: `cbcfaa65`.** History since BRIEF-9's `42784c17`:
  `c3edceb2` (merge P1 `rg55-profiler-daemon`@`637b8c09`, RW-48's
  leaked-session-thread teardown fix — test/conftest-only, no
  `lib/serve.py` production change), `d4f51bbc` (LOG/REPORT: merge
  notes + a FRESH RW-48 proof run on this tree, not copied from P1's),
  `cbcfaa65` (LOG: r2 launch details). This BRIEF's own commit will move
  the tip one further — see "Resume-identity hazard" below, it matters.
- Working tree clean at every commit above (`git status --short` empty
  each time it was checked).
- **C1–C9 complete, CP-2/CP-4..CP-12 all `fixed`** (CP-1/CP-3 stay open
  per RW-27, out of scope). r0/r1 was 100% line+branch at `42784c17`
  (session 8) and a plain `pytest tests -q -x` re-confirmed **1345
  passed, 0 failed** post-merge at `d4f51bbc` — but the REGISTERED r0-r1
  gate lane (`./run-gate.py r0-r1`, coverage-measured, the tool's own
  table) has NOT been re-run since the merge. Do that on the final tip,
  not before r2 lands (Step 2 below), per the dispatch's own Step 4
  ordering.
- Live probes (a)-(f): all done (session 8), one real bug found (CP-12)
  and fixed+re-probed live. Do not redo.

## RW-48 proof, done fresh on THIS tree (cite, do not re-derive)

Backed up `lib/serve.py`, hand-applied `daemon=True -> daemon=False` at
line 689 (the session-loop sampler thread — confirmed via the
surrounding `_dispatch`/`start` code, distinct from the unrelated
`watch`-connection thread at line 1888), ran `timeout 300 python3 -m
pytest tests -q`: **1 failed, 1344 passed, 172.83s** — no hang, well
under the 300s ceiling. The one failure is
`TestStartRegistry::test_start_then_stop_reports_finished`'s `assert
sess.thread.daemon is True`, the exact honest-kill assertion RW-48
added. Restored the file immediately after; verified `git status
--short` and `git diff --stat` both empty before proceeding. Full
narrative in the LOG's "Session 10" section, commit `d4f51bbc`.

## r2 — launched, running unattended, NOT to be touched or waited on this session

**Launch:** bare (no `--base`) — BRIEF-9 said `--base rg55-profiler-daemon`
but that is WRONG: the dispatch itself corrects it ("the `r2` lane is a
COMMAND lane; run it BARE, `--base` is refused") and `run-gate.py --help`
confirms ("A lane that does NOT delegate refuses --base"; r2 is
`kind=command`). Command used (matches P1's own pattern exactly):

```
cd /workspaces/vbpub/.worktrees/rg55-followups-cgprofile/scripts/cgroup-profiler
nohup nice -n 19 ionice -c 3 python3 ./run-gate.py r2 \
  > /tmp/claude-1003/-workspaces-vbpub/5d55184a-d2df-482e-aa2b-541cae13c0ad/scratchpad/p6-r2.log 2>&1 &
disown
```

- **Owner pid: `4136306`** (the `python3 ./run-gate.py r2` process itself
  — NOT the shell-wrapper pid `4136305`, which is not useful to track).
- **Container: `run-gate-vbpub-r2-4136306-1789246893`** — exact name,
  confirmed via `docker ps --no-trunc`. `docker update --cpus=3` already
  applied (`NanoCpus: 3000000000` confirmed). **Never touch any other
  container by image/label/prune while this or any other lane runs**
  (RW-47's exact-name-only rule) — in particular P1's own
  `run-gate-vbpub-r2-3677631-…` is a SEPARATE run, still alive as of
  this cut (`docker ps` showed both "Up ~1h" at cut time); leave it
  alone regardless of this package's own state.
- **Launch log:** `/tmp/claude-1003/-workspaces-vbpub/5d55184a-d2df-482e-aa2b-541cae13c0ad/scratchpad/p6-r2.log`
  — this is THIS session's own scratchpad; it may not exist/be readable
  from a different session's scratchpad path. If it is gone, the
  progress/verdict files below are the durable source of truth, not this
  log.
- **Progress file (durable, in the worktree):**
  `scripts/cgroup-profiler/.assay/r2-progress.jsonl`
- **Verdict file (durable, in the worktree, appears only at a terminal
  outcome):** `scripts/cgroup-profiler/.assay/verdict-r2.json`
- First `run` event confirmed judging commit
  `d4f51bbcf9da81b471c67ce7616342fcf10e5b5d` (i.e., launched from the
  tree AS IT WAS at `d4f51bbc`, one commit before this session's own
  `cbcfaa65` LOG commit — see "Resume-identity hazard"). `budget_s:
  14400.0` (4h advisory lane budget), `budget_per_candidate_s: 600.0`
  (RW-28's per-candidate ceiling — already correctly set from the P1
  merge, nothing to fix here).
- **`candidate_total: 484`** — read from the first `candidate` progress
  lines, NOT the ~230 the dispatch guessed (that estimate was P1's
  daemon-only candidate count; this branch's C1-C9 additions — socket
  carrier, watch, placement, liveness, damon changes — roughly double
  the mutable surface). **Rate observed at cut time: 15 candidates in
  ~1757s of run elapsed time (jobs=2)** — i.e. roughly 117s/candidate
  wall-clock. Extrapolated naively, 484 candidates at that rate is
  **~15.7h**, well past the 4h `budget_s`. **This run will very likely
  hit `BUDGET_EXCEEDED` before finishing all 484 candidates** — do not
  be surprised by it; RW-28's ruling stands ("budget hits are answered
  by a same-tree resume, never by editing the tree") and RW-48's ruling
  ("if it is a lane budget, relaunch once at the SAME tree") applies
  verbatim. If a `budget_exceeded` VERDICT names one specific hung
  CANDIDATE (not the whole-lane budget), that is the RW-48/RW-50 class —
  read the verdict's own detail before assuming which one it is.
- **One survivor already visible in the progress stream at cut time**
  (informational only — do NOT start triaging before the run reaches a
  terminal state; RW-41's post-triage rule says a test-adding commit
  forces one full re-run, so triage happens ONCE, after the final
  verdict): `candidate_index: 13`, `lib/damon.py:329`, `False->True`
  (`python:bool-const-flip`), `outcome_bucket: "survived"`. There will
  be more by the time this is read.

### Resume-identity hazard — read before touching this worktree

RW-41 (`assay resume keys on the WHOLE judged tree's content; a
records-only commit invalidates it`) applies here **exactly**. What
happened, in order:
1. r2 launched while the tree was at `d4f51bbc` — that is the commit
   baked into the run's own baseline snapshot and what `.assay/
   mutation-state/` will be keyed against.
2. Session 10 then committed `cbcfaa65` (the r2-launch LOG entry)
   **after** the lane had already started — a mistake, self-flagged in
   the LOG. This moved HEAD past the judged tree while the lane was
   live. It does NOT affect the currently-running job (already
   snapshotted independently), but it means a **resume** attempt
   (`./run-gate.py r2` again, which assay always runs with an implicit
   `--resume`) will most likely see `resumed_total=0,
   rejected_total=<all>` — the exact RW-41 P2 experience — because the
   live worktree no longer matches the judged tree byte-for-byte.
3. **This BRIEF's own commit moves HEAD a third time**, per the
   controller's explicit instruction (a docs commit doesn't touch the
   judged source, but it does move HEAD). The controller has already
   accepted the consequence: **if a resume is needed and assay refuses
   it, re-judge from scratch at THIS brief's own tip** (whatever commit
   results after this file is committed — check `git log -1` on arrival,
   don't assume a specific hash) rather than trying to force a resume
   that won't take.

**Practical instruction for whoever reads this next:** do not commit
anything else to this worktree until you have checked r2's actual
terminal state (see below) AND decided whether you are about to
relaunch it. If you ARE about to relaunch (fresh or resume attempt),
launch first, THEN write your own checkpoint/LOG commit afterward if
you must checkpoint again before it finishes — same hazard, same
tradeoff, already priced in twice now.

## Exact next steps

**1. Check r2's actual state — read-only, no side effects:**

```
docker ps --no-trunc --format '{{.Names}}\t{{.Status}}' | grep run-gate-vbpub-r2
ps -p 4136306 -o pid,etime,cmd --no-headers   # empty output = process is gone
tail -5 scripts/cgroup-profiler/.assay/r2-progress.jsonl
cat scripts/cgroup-profiler/.assay/verdict-r2.json 2>/dev/null   # exists only at a terminal outcome
```

- If the container for `4136306` and the pid are both still present and
  `verdict-r2.json` does not exist: **still running** — either wait
  (tracked watcher: `until ! kill -0 4136306 2>/dev/null; do sleep 120;
  done`) or, if the operator/controller again says wind down before it
  finishes, write BRIEF-11 the same way this one was written (do not
  touch the running lane).
- If the pid is gone and `verdict-r2.json` exists: **read the verdict in
  a SEPARATE step** (never a pipe tail) — this is the real terminal
  outcome. Proceed to step 2 or 3 below depending on what it says.
- If the pid is gone and NO verdict file exists, or `docker ps` shows
  the container `Exited`: the run likely crashed or was killed
  externally (check `docker logs run-gate-vbpub-r2-4136306-…` and the
  launch log if it still exists) — capture the exit evidence, remove
  the exited container BY ITS EXACT NAME ONLY, and decide fresh-launch
  vs. resume per RW-26's orphaned-container handling (let a still-
  progressing container finish; only clean up one that has actually
  exited).

**2. If the verdict is a clean PASS (0 unjustified survivors) or a
survivor list with no `budget_exceeded` candidates:** go straight to
survivor triage (step 3).

**3. If the verdict is `BUDGET_EXCEEDED`:** read which class it is
(RW-28 wording: check whether one specific candidate sits in
`budget_exceeded` — the hung-mutant class, needs a root-fix + killing
test like RW-48's own — vs. the whole lane simply running out of its 4h
`budget_s` with every candidate otherwise classified normally — the
class this run is actually likely to hit, given the 484-candidate/
~15.7h extrapolation above). For a plain lane-budget exhaustion: relaunch
ONCE at the same tree (`./run-gate.py r2` again — it will attempt an
implicit resume; per the hazard above it will very likely reject and
re-judge from scratch, which is fine, just slower and disclosed). For a
`budget_exceeded` CANDIDATE (RW-48 class): root-cause it the same way
RW-48 did (usually a leaked non-daemon thread or a genuinely
non-terminating mutant needing a killing test), commit the fix, then
launch ONE full fresh run.

**4. Survivor triage** (RW-20/RW-22 standard): every survivor gets
EITHER a killing test OR a written equivalent-mutant justification in
the REPORT explaining WHY the mutant's behavior is observably identical
— prefer honest justifications over forced tests. Record the survivor
table in the REPORT. If triage adds even one test, the tree changed —
per RW-41's post-triage rule, run r2 ONE more full time (alone on the
host, no second concurrent lane) to get a real final verdict; an
equivalent-mutant-only triage (no code/test changes) needs no re-run.

**5. r0-r1 (bare) + r3, on the FINAL tip, verdicts read in SEPARATE
steps** (never a pipe tail): `./run-gate.py r0-r1` and `./run-gate.py
r3`. Expect green (a plain `pytest -q -x` was already 1345/1345 at
`d4f51bbc`; nothing in the merge or the RW-48 proof touched production
code, so this should hold on the final tip too, but it must be run for
real and read from the tool's own table — do not substitute the earlier
plain-pytest result for the registered gate).

**6. Write `cgprofile-P6-FOLLOWUPS-REVIEW-HANDOFF.md`** (does not exist
yet in this worktree — checked, only `cgprofile-P1-DAEMON-REVIEW-
HANDOFF.md` and `cgprofile-P1-DAEMON-HANDOFF.md` exist as siblings; use
`cgprofile-P1-DAEMON-REVIEW-HANDOFF.md`'s shape as the template). Base =
P1's tip (`637b8c09`, i.e. describe the diff from there, since P1 is
already reviewed/released separately) → this branch's final tip. Cover:
C1-C9 + CP-2/CP-4..CP-12 summary (cite the LOG/REPORT sections, do not
re-narrate), the merge conflict resolution (`c3edceb2`, both sides kept,
no production-code conflict), RW-48's proof re-run on this tree, r2's
final survivor table, r0-r1/r3 verdicts, the version/CHANGES sweep C9
already did (1.1.0 goldens), and a pointer for the reviewer to re-run
the frozen cross-package fixture check (`run-gate-project/nyxloom-trove/
fixtures/rg55/*-v1.json`, RW-45(b): only the version string may differ,
`1.0.0`->`1.1.0`).

**7. Return for a FRESH Opus reviewer** (never a fork — this package's
own doctrine, matches vbpub AGENTS.md's model-selection rule) with that
handoff.

**8. After ACCEPT:** merge to `main` (`--no-ff`), then `cmru release
--project cgroup-profiler --set-version 1.1.0` (project id confirmed —
`cmru.toml`'s `[project]` block, matches the root orchestration
registration), then `ciu up` the daemon from `main` so the deployed
instance actually runs the fixed build (CP-12's kill-finalize fix and
everything else in this branch is inert until redeployed).

## What NOT to do

- Do not touch P1's container (`run-gate-vbpub-r2-3677631-…`) or any
  container that is not this package's own exact name.
- Do not re-run the live probes (a)-(f) — done, no bug remaining open
  from them.
- Do not re-derive the RW-48 proof narrative — cite `d4f51bbc`'s LOG
  section.
- Do not start survivor triage before r2 reaches an actual terminal
  state (verdict file exists) — the progress-stream survivor visible at
  cut time is informational only.
- Do not assume the dispatch's original "~230 candidates, 3-5h" estimate
  — this branch's real count is 484 and the observed rate extrapolates
  to ~15.7h; a budget-exceeded outcome on the first attempt is the
  expected case, not a surprise requiring escalation.

## Self-authored retention prompt (paste into the successor's context)

KEEP: this BRIEF in full; the "Resume-identity hazard" section
verbatim (it is the single most load-bearing fact in this hand-off);
the r2 launch identifiers (pid, container name, progress/verdict file
paths); the RW-48 proof summary (one paragraph, cite `d4f51bbc` for the
full transcript); the 484-candidate/~15.7h rate observation; the
"Exact next steps" numbered sequence 1-8; C1-C9/CP-2..CP-12 status as
"done, do not redo."
DROP: session 10's own tool-call-by-tool-call narrative; the orientation
reading it did at dispatch (BRIEF-9, the controller-log rulings) — this
BRIEF already distills everything from them that still matters; the
exact wording of the controller's wind-down message.
