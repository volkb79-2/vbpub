# run-gate-WAVE-RG55-P7 — assay B091: judge candidates by progress, not by time

**Implementer:** FRESH Sonnet session, checkpoint clause on. Package P7 of the
RG-55 wave (design D-17/D-23; controller ruling RW-29). Records:
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P7-{LOG,REPORT,BRIEF-n}.md`.

## Where you work
```
git -C /workspaces/vbpub worktree add .worktrees/assay-liveness -b assay-liveness main
```
Project dir `assay/` (src `assay/src/assay/`, tests `assay/tests/`, docs
`assay/docs/CONSUMERS.md`, backlog `assay/nyxloom-trove/4-backlog.md`, gate:
assay's OWN in-repo gate — find it in `assay/run-gate.toml`/`assay/cmru.toml`
and its `README`/`AGENTS.md`; read the gate verdict in a SEPARATE step). Work
only inside the worktree. Never touch `run-gate-project/`, `scripts/
cgroup-profiler/`, `ciu/`, `/workspaces/dstdns`, or other worktrees.

## Orientation (in order)
1. `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md`
   §1, §2 (D-17, D-23), §4 step 5, §6.
2. `assay/nyxloom-trove/4-backlog.md` **B091** (your spec: contract items
   1–5 and oracles), **B090** (the incident), **B088** (resume identity),
   **B012** (progress NDJSON, `budget_per_candidate`, resume/sharding — what
   already exists and its shape).
3. `assay/docs/CONSUMERS.md`: the progress NDJSON section, the R2 verdict
   schema (v10), the classification table (`Timeout` → `budget_exceeded`,
   never `killed`), `budget_per_candidate` and the R2-admission table.
4. `assay/src/assay/mutation.py` (candidate execution, budgets, progress
   events, classification), `config.py` (`judge.mutation.*` validation),
   `cli.py` (`run … --resume`), the python runner path, the verdict writer.
5. `assay/CHANGES.md` `[Unreleased]` conventions; the version policy
   (`>=` floors) in `~/.claude/CLAUDE.md` if you add a dependency (you should
   not need one).

## Deliverables (one commit each, tests first)
- **A1** `budget_per_candidate = "auto"` default: derived = max(3 × baseline
  wall, baseline + 60 s); printed in the plan line; `budget_per_candidate_
  derived_s` in the verdict; `"none"` → WARN + unbounded; explicit durations
  unchanged; the R2-admission table wording updated.
- **A2** `os._exit(rc)` runner wrapper for the python/pytest runner (and the
  baseline run); document why (design D-23) inline; other runners untouched
  and documented as such.
- **A3** `hung` outcome: (a) runner reported completion (pytest summary line
  parsed from the captured tail) but process alive after a 30 s grace;
  (b) no CPU-time growth in the candidate's process tree (`/proc/<pid>/stat`
  utime+stime incl. children, sampled every 5 s) for max(60 s, 2 × slowest
  test) since the last output line. Kill the tree, classify `hung`, report it
  like `budget_exceeded` in the lane verdict (never `killed`), name it in the
  verdict's survivor/triage list. Schema: add `hung` to the outcome enum in
  the v10 verdict (additive; document in CONSUMERS' migration notes).
- **A4** progress stream: `plan` event (`baseline_s`, `slowest_test_s`,
  `expect_next_event_within_s` = max(3 × slowest test, 15 s),
  `budget_per_candidate_s`, `derived: true|false`) and one `test` event per
  test from a pytest hook assay registers itself (`-p assay._progress` or the
  equivalent plugin injection the runner already uses); documented in
  CONSUMERS as "the progress protocol" for run-gate's watch.
- **A5** `run --resume --rejudge <id>[,…]` and `--rejudge-outcome
  hung,budget_exceeded,error`: drop matching state records before resuming;
  refuse an unknown id; cross-reference B088 (not fixed here).
- **A6** backlog B091 → FIXED with evidence (hashes), B090 → note "mitigated
  by B091"; CHANGES `[Unreleased]` entries; CONSUMERS + README.

## Gates
assay's own gate lanes as registered in-repo (coverage judge: 100% line AND
branch on changed lines — the estate bar), then its assay-judged R1/R2/R3
lanes if it has them; mutation lane LAST, one at a time, launched untracked
(`nohup … > <scratchpad>/p7-r2.log 2>&1 & disown`) with a cheap tracked
watcher. Every survivor: a killing test or a written equivalence
justification. Do NOT run `cmru release` — the controller releases 6.2.0.

## Records, checkpoint clause, BLOCKED protocol
As in `run-gate-WAVE-RG55-P4-HANDOFF.md` (same wave): LOG per commit
(self-hash rule), REPORT per deliverable with the B091 oracle → test mapping
and mutation-check transcripts, E-002 telemetry; ARM ~120k context / ~60
calls, CUT at a coherent boundary, BRIEF-n + return; decision asks never stop
you (RW-9) — take the option B091/D-23 favours, log it, continue.

## HOST LOAD (binding)
8 cores / 16 GiB shared with a PRODUCTION game server; **RAM PSI is the
limit**: read `/proc/pressure/memory` before any whole-suite run and back off
(wait, retry in 5 min) while `full avg10 > 5`. pytest serial, `nice -n 19
ionice -c 3`; targeted files while iterating; whole suite at most once per
commit that needs it. Other tracks run their own mutation lanes concurrently
(P1, P2, P4): never start a second mutation run of YOUR project; ≤ 2 gate
containers estate-wide (`docker ps` for `tester-unified:local`); `docker
update --cpus=3` after any launch you make; remove in a `finally`; never
`--cgroupns=host`/`--pid=host`.

Commit trailers: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`,
`Claude-Session: https://claude.ai/code/session_01YBJzBA7KyG4ayu5ndNf9Hx`.
Edit tool for file changes; `git commit --only -- <paths>`. Claim only what
you ran — a fresh adversarial reviewer verifies every claim.
