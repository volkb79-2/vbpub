# run-gate wave — resumable, observable gate (E-1 of the post-v10 plan)

**Status: DISPATCHED 2026-09-02** by the vbpub controller session on the
operator's instruction ("your design choices are all accepted … can you kick
off any run-gate things in parallel already?"). Plan of record:
`assay/nyxloom-trove/WAVE-PLAN-2026-09-02-after-v10.md` (decisions D1–D7,
all accepted as recommended). Target: **run-gate 23.4.0, `__revision__ = 34`**.
Branch `feature/run-gate-wave-resumable`, worktree
`/workspaces/vbpub/.worktrees/run-gate-wave-resumable`, forked from `main`.

Independent of assay: nothing here needs an assay change or a verdict-schema
change. assay's Wave D (v10) runs concurrently on its own branch; the only
shared resource is the host (see HOST LOAD below).

---

## Scope

| item | ruling | one line |
|---|---|---|
| **RG-35** re-attach | RW-1..RW-3 | a lane's container outlives a dead client; record it, re-attach or collect on restart, never start a duplicate |
| **RG-36** progress-judged liveness | RW-4..RW-6 | tail `.assay/progress-<lane>.jsonl`: periodic rate/ETA line; optional `stall_timeout`; COARSE timing now (file mtime + run-gate's clock), exact when assay B065 lands |
| **RG-32** inert `pins.assay.budget` | RW-7 | refuse the key at load by name; the owner is the consumer's `assay.toml` lane |
| **RG-34** unprefixed script path in a command lane | RW-8 | `doctor` names it; the argv fix itself is the consumer's |

Excluded: **RG-38** (durable state dir — needs assay B066 first, per D5),
**RG-37** (index row only, no section to implement from), **RG-18**
(dstdns-side). Nothing else from the backlog is pulled in.

## Rulings (RW-n; do not improvise past them — ask)

- **RW-1 (D4) — re-attach is automatic and disclosed.** On a successful
  `docker run -d`, write `.run-gate/inflight/<lane>.json` (git-ignored under
  the R-36 store discipline; refuse to write it if `.run-gate/` is not
  ignored, with the same remedy `history.json` gives): container name + id,
  `started_at` (UTC ISO), judged commit, worktree path, project dir, verdict
  path, progress path, run-gate `__revision__`. On invocation of the same
  lane + worktree: if the record's container EXISTS and its recorded commit
  equals the judged worktree's HEAD → re-attach (`docker logs -f --since
  <started_at>` then `docker wait`), printing
  `run-gate: re-attached to <name> (started <t>, running <elapsed>)` before
  anything else; if the container has EXITED → collect exit code, logs
  (evidence on failure, R-26) and finish exactly as an attached run would,
  printing `run-gate: collected <name> (exited <code> at <t>)`; if the
  record's container is GONE (host reboot) → print that, clear the record,
  run fresh; if the record's commit differs from HEAD → refuse (exit 2)
  naming both commits and `--fresh`. `--fresh` removes the recorded
  container first (disclosed by name) and runs anew. The record is cleared in
  the same `finally` that removes the container. `--dry-run` discloses an
  inflight record when present and starts nothing.
- **RW-2 — one record per (lane, worktree)**, the scope RG-27 already uses;
  concurrent writers answered the same way (sibling lockfile + atomic
  rename). Conjunction lanes carry the behaviour to each sub-lane; the
  conjunction itself has no container and no record.
- **RW-3 — history (RG-27) records a re-attached or collected run ONCE**,
  with duration from the record's `started_at`, outcome from the real exit
  code. A collected run whose evidence is already gone (container removed
  by someone else) is recorded as `aborted`, never as a pass.
- **RW-4 (D3) — liveness is judged here, from the file.** While a container
  runs, read `<project>/.assay/progress-<assay_lane>.jsonl` (the path R-38
  constructs) every `PROGRESS_POLL_SECONDS = 30` (module constant with a
  reason) and print, at most once per poll when something changed:
  `run-gate: progress <lane>: candidate <i>/<N>, <rate>/min, ETA <m>m` —
  `i`/`N` from the newest event's `candidate_index` / `candidate_total`,
  rate from run-gate's OWN clock since the first event it observed (no
  timestamps exist in the file today — B065 adds them; when an event carries
  `elapsed_s`, prefer it, so the same code becomes exact without a rewrite).
  No file, or a file with only the `run` header (R0/R1 lanes), is disclosed
  ONCE as `run-gate: progress <lane>: no candidate events (not an R2 lane, or
  the judge writes none)` and never treated as a fault.
- **RW-5 — `stall_timeout = "15m"`**, optional lane key with the `budget`
  duration grammar: the lane is stopped only when the container is STILL
  RUNNING and the progress file's mtime (or newest `elapsed_s`) has not
  advanced for that long. Stop = `docker rm -f`, evidence saved, exit 3
  naming the stall, the last event seen and the age. NEVER on total elapsed
  time — `budget` stays advisory and its print unchanged. A lane without a
  progress file cannot stall by this rule (RW-4's disclosure says so).
- **RW-6 — docs say the shape:** for a mutation lane, a generous assay
  `budget` + `judge.mutation.budget_per_candidate` + run-gate
  `stall_timeout`; for R0/R1, `budget` is the command's own bound and there
  is nothing to add. (assay B067's `unbounded` is E-4, not here.)
- **RW-7 (RG-32) — refuse, do not rename.** `[lanes.*.pins.*]` gains an
  unknown-key check like `_validate_lane`'s; `budget` under a pin refuses at
  load: `pin 'assay' declares 'budget' — run-gate never enforced it; the
  lane's budget lives in the consumer's assay.toml [lanes.<assay_lane>]
  (delete this key; the lane-level run-gate 'budget' stays advisory)`. A
  `budget_hint` + cross-check was rejected: a second reading of an
  assay-owned fact (R-35's rule). Documented as a BREAKING config change
  with its migration (RG-23's precedent), because dstdns's config carries
  the key today.
- **RW-8 (RG-34) — `doctor` warns; run-gate does not rewrite argv.** A
  `kind = "command"` lane on a container environment whose argv[0] is a
  relative path containing `/` and not starting with `{worktree}` gets ONE
  `[WARN]` in `doctor` naming the lane, the element, and the fix
  (`"{worktree}/<path>"`), with RG-34's mechanism in one sentence (a
  dedicated container that mounts only the worktree has nothing at the bare
  repo root). Warning, not refusal: the same argv is correct under a
  full-repo mount, which run-gate cannot see statically. SPEC records it as
  `R-30b` beside `R-30a`.

## Roles

- **Implementer** (fresh Opus): one package, the four items above in the
  order RG-35 → RG-32 → RG-34 → RG-36 (RG-36 last because it shares the
  container loop RG-35 rewrites). Records under
  `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RESUMABLE-{LOG,REPORT}.md`;
  E-008 checkpoint clause → `-BRIEF-n.md` + a fresh successor.
- **Reviewer** (fresh, never a fork): adversarial, 3-round cap, on the
  gate-green tip; reads the diff blind first; runs one LIVE re-attach probe
  (kill -9 the client mid-lane, second invocation re-attaches) under the
  host rule; writes `run-gate-WAVE-RESUMABLE-REVIEW-round<n>.md`.
- **Controller**: rulings RW-9.. in the controller log
  (`run-gate-WAVE-RESUMABLE-CONTROLLER-LOG.md`), merge `--no-ff`, `cmru
  release --project run-gate-project` (→ 23.4.0), dstdns notify, CHANGES
  housekeeping.

## Gate and evidence

- run-gate's own gate is the host lane `selftest`
  (`run-gate-project/run-gate.toml`): from the worktree's
  `run-gate-project/`, `nice -n 19 ionice -c 3 ./run-gate.py selftest
  --allow-dirty` (pytest serial + `tools/coverage_gate.py` diff-coverage
  floor against `main`). Green = exit 0 and `diff-coverage OK`. Read the
  verdict in a separate step from the log.
- Every new docker argv shape needs ONE live acceptance probe (AGENTS.md:
  "pinned against a fake docker proves construction, not acceptance"): the
  re-attach path runs a real `tester-unified:local` container once. Host
  rule applies: `docker ps --format '{{.Image}}'` must show no
  `tester-unified:local` and no `run-gate-vbpub-*` container before you
  start one (assay's Wave D gates run on this host); cap yours
  (`docker update --cpus=3 <id>`); remove it in a `finally`.
- Fake-docker tests carry the rest (the existing `fake_docker` /
  `fake_docker_executing` shims and `_docker_argv_line`).

## HOST LOAD (binding)

8 cores shared with a production game server; load hit 85 today. pytest
SERIAL only (never `-n`), always `nice -n 19 ionice -c 3`; targeted test
selection while iterating, the whole selftest at most once per checkpoint
and once before the return; one container at a time across every agent on
the host; no build/pip step concurrent with a suite run.

## Rules

- File edits through the Edit tool, never sed/python rewrite scripts.
- `git -C <worktree> commit -F <msgfile> --only -- <paths>` (new files `git
  add`ed first) with both trailers `Co-Authored-By: Claude Fable 5.1
  <noreply@anthropic.com>` and `Claude-Session:
  https://claude.ai/code/session_01RJ3wqoyy8ZzHmj7ZK1qEnJ`; never `cd
  /workspaces/vbpub` before a git command; never a bare `git stash`.
- `__revision__ = 34` with the RG-35/36/32/34 note prepended in the existing
  style; SPEC gains `R-39` (re-attach), `R-40` (progress-judged liveness),
  `R-30b` (RW-8), and the pin-key rule under `R-04`/pins; README's lane
  schema gains `stall_timeout`; CHANGES `[Unreleased]` hand-written entries
  incl. the RG-32 breaking note; backlog rows/sections → FIXED with the
  measured evidence; `tests/test_run_gate.py` for every behaviour, red-first
  where a pre-fix implementation is expressible (the current
  `run_container_lane` IS the controlled wrong implementation for RG-35).
- No push, no merge, no release — the controller does those.
- Decision asks that the rulings do not settle go in the REPORT and the
  return message; do not decide product questions on silence.

## Release (controller)

Merge `--no-ff` to main after the reviewer's ACCEPT; push; `cmru release
--project run-gate-project --set-version 23.4.0 --allow-uncommitted`
detached with a Monitor; `git merge origin/main` if the final rebase trips
on another session's dirt; clear `[Unreleased]` by hand; notify dstdns-23
(re-copy is not needed there — they install the wheel — but `pins.assay.budget`
must be deleted from their run-gate.toml before upgrading, RW-7).
