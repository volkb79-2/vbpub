<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-07; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/shard-s4-FINAL-REPORT.md -->
<!-- relation: CMRU final R2 shard report; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Shard s4 (cmru R2) moved to the controller: FINAL REPORT (2026-11-08, 13:46Z)

## Result

All 305 candidates are judged, except one that stays `budget_exceeded`. No shard process is running.

| Bucket | Count |
|---|---:|
| killed | 283 |
| survived | 21 |
| budget_exceeded | 1 |
| crashed / hung / equivalent | 0 |
| total | 305 |

- Survivors by file: `cmru/src/cmru/git_auth.py` 11, `cmru/src/cmru/versions.py` 8, `cmru/src/cmru/hosts/__init__.py` 2.
  Ids and operators are in `shard-move-s4/final/survivors.json`.
- The one `budget_exceeded` candidate is `30580dc1d4ef3134956c9aa4609d2193caa58f61d7f7a16137543d9246e6ba98`
  (`cmru/src/cmru/versions.py`, `And->Or`, `python:boolop-swap`). It was recorded on <host> on 2026-11-07 (1861 s wall against the roughly 1714 s per-candidate budget).
  Resume does NOT re-run it: the 13:25Z attempt printed `resumed_total 305, pending_total 0`.
- **No final lane verdict file exists.** The lane verdict is `BUDGET_EXCEEDED/LANE_TIMEOUT` (exit 4) and the closeout says
  "campaign incomplete, planned 305, completed 304, pending 1". Blockers: `coverage_not_reverified`, `lane_timeout_or_unstarted`.
  Every attempt writes this verdict because of the one budget_exceeded candidate, so more resumes only repeat a roughly 20 min baseline (about 10 min per baseline pass)
  and the same verdict. I stopped the supervisor at 13:45Z for that reason.
  The verdict JSON written at 13:35Z was deleted when the next attempt started. A killed attempt 3 therefore left none,
  and the judged verdict path is not available.
  What is preserved: `final/0ccc8e7af8cb2741df0fcc04f3d68b41.campaign.json` and `.txt` (the campaign analysis for the 13:35Z closeout),
  `final/progress-cmru-r2-s4-final.jsonl` (all 305 candidate events, 4 `resume` events, `verdict_written` events) and `final/profile-cmru-r2-s4.jsonl`.
  A decision is needed on how to treat this one candidate (re-judge it, for example by a one-candidate run without `--resume`, or
  accept it as `budget_exceeded` with a note). I did not improvise that.

## Timeline (wall time on the controller)

- 02:12Z start on controller (resume of the <host> shard, 209 done). Attempt A ran until the 7 h lane budget, 09:12:37Z (exit 4, no OOM). Devcontainer was OOM-killed at 03:41Z, but the shard container survived.
- 09:12Z: 283 done, 22 pending.
- 11:02Z: two resume tries only "followed" the dead run. The stale inflight record (a different PID namespace after the OOM restart) was moved to `shard-move-s4/inflight-record-attempt1-stale.json`.
- 11:03Z attempt 2: stopped by the guard at 11:17:51Z. Memory PSI some avg10 rose to 28.9, 33.7 and over 25 again, while MemAvailable was about 5.7 GiB.
- 11:23Z attempt 3 (guard rules unchanged, plus `watch-ps.sh`): `resume` at 11:42:46Z with resumed 283, rejected 0, rejudged 0, pending 22. It was not a new 305 plan.
  Memory PSI stayed below 4 the whole time. All candidates processed by 12:56:48Z (`end` event). Candidates: 22 in 74 min after the baseline (about 18 per hour).
- 13:07Z and 13:36Z: the supervisor looped, as designed, on the exit-4 verdict. Each loop was baseline only (pending 0).
  I stopped the supervisor, the container (`docker stop` by exact name) and the guard and watcher at 13:45Z.

Total controller wall time from first start: about 11.5 h (02:12Z to 13:46Z), of which about 7 h lane budget in attempt A and about 2.4 h in attempts 2/3.
Candidate work on the controller: 96 pending at the move, all but the one stuck candidate resolved.

## Anomalies

- The 11:17Z guard trip came at the start of the second phase, right after a baseline pass finished. It was not seen again (the 11:42Z second-phase start was calm,
  with fewer other agents running). Cause was never attributed; no process list was captured at that trip because `watch-ps.sh` was added after it.
- run-gate 25.0.2 lane label lookup does not work, so `guard-s4.sh` (name based) did the guarding and limits were applied by hand with `docker update`.
- The `budget_exceeded` bucket in the `end` event counted 23 in attempt A: 1 real candidate plus the 22 pending.
- The <host> sat queue flag was removed earlier by the pilot agent (see `shard-move-s4-REPORT.md`). That is untouched here.

## State left behind

Nothing is running (no s4 container, supervisor, guard, watcher or monitor). The earlier exited container
`...-assay-r2-s4-75452-1794103952` and the logs are kept. `shard-move-s4/STOPPED-BY-CONTROLLER-AGENT-complete-except-1-budget-exceeded` marks the final stop. The checkout
`<worktree>` and the campaign directories are intact; nothing was cleaned up.
Evidence under `<controller-program-dir>/shard-move-s4/final/`; guard and monitor logs `guard.log`, `samples.csv`, `watch-ps.log` in the parent directory.
