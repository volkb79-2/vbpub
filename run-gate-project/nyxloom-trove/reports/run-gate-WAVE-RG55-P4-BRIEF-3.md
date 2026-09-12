# run-gate-WAVE-RG55-P4 — BRIEF-3 (return, session 3: `assay-r2` occupied)

Return reason: every gate this package's handoff names EXCEPT `assay-r2`
is now GREEN and evidenced. `assay-r2` cannot proceed — run-gate-project's
own `r2` (mutation) lane is occupied by P2's own resume (pid `1499375` at
the time of this cut, running from P2's OWN worktree,
`.worktrees/rg55-run-gate-client` — confirmed via `/proc/1499375/cwd`, not
guessed). Per the dispatch prompt's own explicit rule ("If occupied: write
'r2 pending'... and RETURN with the tip hash and the selftest/r1/r3
verdict lines; the controller re-dispatches for r2"), returning now rather
than waiting or attempting it.

## State

Tip: **`7539a44e`** on branch `rg55-followups-run-gate`, worktree
`/workspaces/vbpub/.worktrees/rg55-followups-run-gate`. Working tree
CLEAN. Commits this session (session 3, on top of BRIEF-2's tip
`b695db00`):

1. `af654ede` — fix: `test_no_stdlib_violations` missing `"resource"`
   (C4's own gap — `import resource` for RG-57's rusage path was never
   added to the anti-goal test's allowlist). First real `selftest` run
   was RED on this alone (coverage judge never even ran — pytest's own
   failure short-circuited the `&&`).
2. `e0e02dce` — 13 new tests closing the wave-diff coverage gap the
   SECOND `selftest` run exposed (97.2%/97.4% → the judge's own 100%/100%
   floor). Every uncovered line traced to C4's own code before writing
   anything (`resolve_self_container_id`'s five branches + its own
   success return; `start_bare_host_profiling`'s three failure guards;
   two `run_record is not None` branches in `run_bare_host_lane`'s own
   `finally`, one only reachable via a DIRECT function call since
   `main()` never builds a `None` run_record for a live invocation).
3. (gate-only, no commit) third `selftest` run: **GREEN** — `1114
   passed, 3 skipped`; `diff-coverage OK: 1008/1008 (100.0%); branches
   376/376 (100.0%)`. This run also produced the real, clean-tree,
   profiled history entry RG-61 item 5 needed.
4. `02707e30` — RG-61 item 5: the real `footprint --write` transcript,
   pasted byte-for-byte into `CONSUMERS.md`; `run-gate.footprint.json`
   committed (TRACKED, first time). **RG-61 is now 100% closed.**
5. `5bdd7a2f` — LOG entries for commits 5-8 above (session 3's own
   re-orientation + Commits 5-8 narrative).
6. `7539a44e` — fix: `tools/canary-run.sh`'s `median-not-mean-series-
   stats` canary had gone stale against `series_stats`'s own pre-existing
   `byte_valued` branch (P2's earlier RW-24/R-36k work, not this
   package's) — the literal `find` text no longer matched, scored as a
   SURVIVED mutant. Re-anchored to current source, same mutation
   semantics (verified: the target test's assertions never touch the
   `byte_valued` arm), same killing test. Confirmed locally
   (`tools/canary-run.sh` — both canaries `ok`) before committing.

Full narrative for every commit: `run-gate-WAVE-RG55-P4-LOG.md`'s own
"Session 3" section (appended this session, after "Session 2 checkpoint").
Full evidence, gate transcripts, footprint transcript, doctor output, docs
disposition table, E-002 telemetry: `run-gate-WAVE-RG55-P4-REPORT.md`
(written this session — everything except `assay-r2`'s own survivor
table, which does not exist yet).

## Gate verdicts this session produced (all VERIFIED, not just run)

- `./run-gate.py selftest` (no `--base` — refused if given; BRIEF-2's own
  confirmed finding, still true): **PASS**, third attempt, after two
  genuine fixes above.
- `./run-gate.py --base rg55-run-gate-client assay-r1`: **PASS**, first
  attempt.
- `./run-gate.py assay-r3` (no `--base` either — ALSO refused if given,
  a NEW finding this session made: R-35 applies to this lane too, not
  just `selftest`, since its argv carries no `{base}` token): **PASS**,
  second attempt, after the canary staleness fix. `duration_stats`'s own
  canary (the item the ORIGINAL dispatch prompt named explicitly) was
  VERIFIED rejected, not just run — see the REPORT's own paragraph on
  exactly how that verification works (reading `canary-run.sh`'s own
  pass/fail mechanism, not assuming "no crash" means "verified").
- `./run-gate.py assay-r2`: **NOT RUN.**

## What the next session must do

1. Re-verify host state fresh (do not trust this brief's own numbers):
   ```
   pgrep -af 'run-gate.py --base main assay-r2|assay.cli run r2'
   docker ps --no-trunc --filter ancestor=tester-unified:local
   cat /proc/pressure/memory
   ```
   If `run-gate.py --base main assay-r2` (or `assay.cli run r2`) still
   shows a process whose `/proc/<pid>/cwd` is `.worktrees/rg55-run-gate-
   client` (P2's own worktree) — or ANY other `run-gate-project` mutation
   run anywhere — **do not start `assay-r2`**; write "r2 still pending"
   in the LOG (with the new pid/etime) and return again. The dispatch
   prompt's own original rule stands: this package does not decide "P2's
   run is clear" on its own judgment past what `pgrep`/`docker ps` show —
   if in doubt, ask the controller rather than guessing from elapsed
   time.
2. If genuinely free: launch `assay-r2` on tip `7539a44e` UNTRACKED
   (`nohup ./run-gate.py --base rg55-run-gate-client assay-r2 >
   <scratchpad>/p4-r2.log 2>&1 & disown` — `budget_per_candidate =
   "900s"` is already set under `assay.toml`'s `[lanes.r2.judge.
   mutation]`, confirmed present, do not re-add it), arm a cheap TRACKED
   `until ! kill -0 <pid>; do sleep 60; done` watcher, and when the
   verdict lands triage every survivor per RW-20/RW-22 (a killing test or
   a written equivalent-mutant justification in the REPORT) — one resume
   after triage (per RW-28: candidate identity keys on source bytes, an
   `.assay/mutation-state/` resume is safe as long as no test-only fix
   masks a stale verdict — delete the specific candidate's state file
   first if a test fix changes that candidate's own judged code).
3. Append the survivor table + mutation-check transcript to
   `run-gate-WAVE-RG55-P4-REPORT.md` (already has every OTHER section —
   do not rewrite it, extend it).
4. Once r2 has a clean verdict (or every survivor is triaged): this
   package is DONE. Return to the controller with the final tip, all four
   verdict lines, and the survivor table (or "0 survivors").

## Self-authored retention prompt (paste into the successor's context)

> KEEP: this BRIEF-3 in full (it is the plan); tip hash `7539a44e`; the
> fact that `selftest`/`assay-r3` BOTH refuse a `--base` flag (R-35, no
> `{base}` token in either lane's argv — do not pass one and assume a
> refusal is a bug); the three genuine fixes this session made and why
> each was this-package's-responsibility-to-fix-regardless-of-blame (the
> `resource` import gap, the wave-diff coverage gap, the stale
> `series_stats` canary — none were silently patched around, each was
> root-caused before touching code); `assay-r2`'s own occupied-lane rule
> (check `pgrep`+`/proc/<pid>/cwd`, never guess from elapsed time alone,
> never start it without confirming free); `budget_per_candidate =
> "900s"` is already correctly placed under `assay.toml [lanes.r2.judge.
> mutation]` (RW-28) — do not "fix" it into a different table name
> (`[lanes.assay-r2...]` is `run-gate.toml`'s OWN lane name, a different
> namespace).
> DROP: the tool-by-tool trace of how each fix was implemented (already
> IN the commits and the LOG, which stays the detailed record — this
> BRIEF is the distilled next-steps map); BRIEF-1/BRIEF-2's own superseded
> "gates have not run yet" framing (all superseded — every non-r2 gate
> now has a real, verified verdict).

## Host load (re-check fresh — do not trust these numbers)

At cut time: PSI `full avg10` 0.0x (comfortably under the RW-39 threshold
for non-mutation lanes), P2's `assay-r2` resume (pid `1499375`, started
~5 minutes before this cut, P2's own worktree) occupying run-gate-
project's shared `r2` lane, `docker ps` showing exactly one OTHER
project's gate container (cgroup-profiler's own P1 resume,
`run-gate-vbpub-r2-680904-…`) — within the estate-wide `≤ 2` cap
regardless.
