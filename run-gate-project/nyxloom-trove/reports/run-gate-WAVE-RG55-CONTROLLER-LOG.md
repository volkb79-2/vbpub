# run-gate RG-55 wave (lane resource profiling) — controller log

Plan of record: `run-gate-project/nyxloom-trove/WAVE-PLAN-2026-09-12-rg55-profiling.md`.
Contract (frozen by the P0 package below): `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`
(mirrored verbatim at `scripts/cgroup-profiler/docs/RG55-INTERFACE-CONTRACT.md`).
Rulings are numbered `RW-n` from `RW-1` for this wave and bind both P1 and
P2; a contract change after dispatch is always a new numbered ruling here,
never a silent edit to the contract file.

## Rulings

- **RW-1 (2026-09-12):** P1 (cgroup-profiler daemon) and P2 (run-gate
  client) are dispatched IN PARALLEL, per plan D-13 — the operator's
  explicit choice in the 2026-09-12 interview ("contract first, two
  parallel packages, then integration"). This supersedes, for this wave
  only, the 2026-09-03 "single agent, single gate" serial dispatch
  directive recorded in the `dispatch` skill. It does NOT relax the
  estate's standing host-load rules: the gate-container cap (at most 2
  across the estate, 3 CPUs each, `docker update --cpus=3` right after
  launch) and "one measurement probe at a time with the daemon idle" still
  bind both packages exactly as the plan's §7 HOST LOAD block states.

- **RW-2:** bare-host lanes are explicitly out of v1 scope (plan D-5) →
  filed as **RG-57** (`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`).
  Conjunction lanes record nothing of their own (plan §3.1) — each member
  lane's own profiling record is the only one that exists; the conjunction
  wrapper itself never gets a `resources` field.

- **RW-3:** the uncommitted one-line fix at
  `scripts/cgroup-profiler/cgprofile.py:685` (`cmd_targets`'s helper spec
  must use `DEFAULT_OUT`, not `HERE`) belongs to another session and stays
  UNCOMMITTED in the shared checkout — this P0 package does not touch it,
  per the "never clobber another session's uncommitted work" rule. P1
  re-applies the identical one-line fix as its own first commit inside its
  own worktree (`.worktrees/rg55-profiler-daemon`); if the original owner
  commits it to `main` first, a later merge of P1's branch resolves
  identically (the same one-line change either way) and is not a conflict
  worth avoiding in advance.

- **RW-4:** the contract is FROZEN at the P0 commit hash
  **`63b928da0770c79409f8ad5faff0b840a6e0cf30`** (`vbpub@63b928da`; this
  line was updated by a second, tiny commit immediately after the P0
  commit landed — the hash could not be known before committing). Any change
  to the contract after this point is a new numbered ruling here, delivered
  to both implementers; nobody edits `RG55-INTERFACE-CONTRACT.md`
  unilaterally.

- **RW-5 (P2, after its first checkpoint):** 0/0 diff semantics corrected.
  As first landed (`607950fd`), a `changed_executable == 0` verdict REFUSED
  (exit 2) unless `--allow-empty-diff` — which makes `./run-gate.py
  selftest` red on `main` itself (merge-base(main, HEAD) == HEAD → zero
  changed lines) and would block every `cmru release` of this project. Now:
  0/0 → exit 0 with a distinct verdict line `diff-coverage SKIPPED: 0
  changed executable lines under '<source>' between <base> and HEAD
  (<relation>)` and `"verdict": "skipped"` in any structured output, never
  `100.0% OK`; refusal is OPT-IN via `--refuse-empty-diff` (the
  `--allow-empty-diff` flag is removed). Rationale: the false-green hazard
  RG-51/RG-54 describe is a WRONG BASE hiding real source changes; the judge
  cannot tell that from "this change has no source lines" by the zero alone,
  so the zero is made visible and named, and the wrong-base guard stays
  RG-51's fork-point rule. Landed as `8c76ba3e`.
- **RW-6:** accepted P2's reading — `branches_total`/`branches_missed` are
  scoped to the changed-line set and reported beside the line counts.
- **RW-7:** accepted P1's reading — "the last read of `memory.peak` /
  `pids.peak`" (contract §7) means the last SUCCESSFUL read (skip back over
  trailing nulls), pinned by a dedicated test in `lib/summary.py`'s suite.
- **RW-8 (P2 C2):** assay judge scope for run-gate-project's new lanes:
  `judge.source_roots = ["."]` with `base_source = "request"`; `tests/` is
  excluded by assay's own `is_test_path`; `tools/` IS judged deliberately (a
  changed line in `tools/coverage_gate.py` must be covered by
  `tests/test_coverage_gate.py` — that is what 100% on every changed line
  means here). No restructuring of `run-gate.py` into a subdirectory.
  Narrowest supported exclusion only if the symlink/fixtures trip the
  adapter; never exclude `tools/`.
- **RW-9 (process):** a decision ask never stops a package — record it,
  apply the reading the handoff/contract/fixtures imply, mark it, continue
  with everything independent of it. (P2's second session stopped on the
  C2 ask with C3–C8 untouched.)

- **RW-10 (P2 C2):** accepted P2's readings — the `assay-r3` canary runs the
  one pytest selector that encodes the invariant (the cgroup-profiler
  `canary-run.sh` shape, not the full r1 argv on a scratch copy), and
  `assay plan r2` stood in for the RW-8 proof because `assay plan` is
  R2-only. assay-r1's first run on the branch FAILED (86.9%) on a real gap
  in `tools/coverage_gate.py` invisible to the narrower selftest — closed
  with four tests (`45f2aa5a`); the lane is doing its job.
- **RW-11 (contract amendment, P2 C3):** contract §4.3's basic-path file
  list gains `memory.max` and `memory.high` (12 files): §7's `limit_drift`
  and the golden `summary-basic-v1.json` both require them. Applies to the
  daemon side as a no-op (it samples every group already). The contract
  file itself (and its mirror) is amended at P3 integration so both copies
  move in lockstep; until then this ruling is the text.
- **RW-12 (P2 C3 wiring):** `await_container` tick shape — no background
  sampler thread. `proc.wait(timeout=tick)` with `tick =
  PROFILE_SAMPLE_SECONDS` while a basic sampler is active, else
  `PROGRESS_POLL_SECONDS`; the progress/log-stream watch polls only when
  ≥ `PROGRESS_POLL_SECONDS` have elapsed since its last poll (monotonic,
  injectable), so RG-36/RG-41 stall semantics are unchanged. The daemon
  path does no per-tick work and no `ctl status` polling in v1.

- **RW-13 (P1 C4):** accepted — `ctl status <id>` on a finished session
  answers `unknown-session`; a finished session's summary is reachable via
  the idempotent `stop`.
- **RW-14 (P1 C4→C5/C6):** `ctl report` must render the REAL report (plan
  D-8, contract §2.6) through the existing report tier inside the image;
  a stub is not acceptable. If the session directory layout deviates from
  what `analyze`/`store` expect, the daemon writes the compatible layout.
- **RW-15 (P1):** the no-token path re-discovers `cgroup.procs` on the
  discovery cadence and recommits DAMON targets on change, like the token
  path.
- **RW-16 (P1):** accepted — `WRITABLE_ROOTS` guards the raw writes
  `serve.py`/`damon.py` issue directly; `RunDir` writes are sessions-dir
  scoped by construction; the socket bind/unlink is control-plane.
- Deferred by ruling: populating `events.jsonl` with detected events →
  **CP-5** (the summary's `events` counts are the contractual part and are
  computed). CP-4 (pre-existing `test_store.py` birthday-collision flake)
  was filed by P1 session 4.

- **RW-17 (P2 C3 wiring):** no test-only switch in production code. The
  `RUN_GATE_TEST_DISABLE_PROFILING` kill switch becomes a documented,
  operator-facing ambient override `RUN_GATE_PROFILE` (`on`|`off`; absent →
  config decides; other values refused by name), the same class of knob as
  `RUN_GATE_CGROUPFS_ROOT`/`RUN_GATE_PROC_ROOT` (a CI runner without
  `docker exec` rights). `off` → no token, no calls, `resources: null`,
  `profile_error: "disabled (RUN_GATE_PROFILE=off)"`; disclosed by
  `--dry-run` and `doctor`; the test suite's autouse fixture uses it.
- **RW-18 (P2):** accepted — container id resolved through the existing
  `container_state()` inspect call (`{{.Id}}` = full 64-hex), not a second
  inspect; pure refactor.
- Noted from P2 session 5: the basic path's final sample ALWAYS failed on
  an ephemeral lane (`docker exec` refuses an exited container), nulling
  `memory.peak_bytes` — found only by the live probe, fixed as
  `sample_final()` (`38089fe6`). A fake-docker construction test could not
  have caught it (AGENTS.md's "argv proves construction, not acceptance"
  again). The P2 reviewer must re-run that probe.

- **RW-19 (P1 r2 budget):** cgroup-profiler's r2 mutation lane has 217
  candidates at ~84 s each with `jobs = 2` (~2.5 h); `[lanes.r2] budget`
  in `assay.toml` and the `run-gate.toml` r2 lane budget raised 45m → 4h
  (`71c6f607`), one resume run allowed; partial verdict + survivors recorded
  if still exceeded.
- **RW-20 (P2 assay-r2 runtime):** run-gate-project's r2 lane has 256
  candidates with the whole ~950-test suite per candidate (`jobs = 1`,
  bare-host) — far beyond 4 h. Resume runs (`--resume` is always passed)
  continue until a final verdict, no cap; the implementer session stays
  alive until then (the lane process is tied to it); survivor triage commits
  land in the reviewer's fix-verification round. The adversarial review of
  the tip `62d9a66a` starts in parallel; the reviewer does not touch
  `.assay/` or run r2 itself.

- **P2 review round 1 (tip `62d9a66a`): REJECT** — B1 profiling exceptions
  escape `main()` and leak the container (four planted routes + a real
  `UnicodeDecodeError` route); B2 `meta.expected` sent as a bare int
  (contract §2.2 wants the four-key object); B3 two surviving mutants on
  contract rules; B4 hollow red-first proof for the exec rewrite; B5 records
  claim a gate sweep that did not run. Fix round dispatched to a FRESH
  implementer (large remaining work; the original session is idle-waiting on
  its r2 lane).
- **RW-21 (contract §7 amendment, from the review's measurement):** scope
  `container` reads cumulative counters as ABSOLUTE values at the last
  successful read (cpu, pressure, faults, events), `peak_over_baseline_bytes`
  is `null` there; scope `container-shared` keeps the delta rules; host
  fields keep deltas in both scopes. Rationale: an ephemeral lane's cgroup
  is born with the lane, so a delta from the first sample drops everything
  before it (3.3× CPU understatement measured live). Goldens
  `summary-container-v1.json` regenerated, `summary-basic-container-v1.json`
  added; landed on `main` by the P2 fix implementer, adopted by both
  packages via `git merge main`. Contract text now carries an Amendments
  line (RW-11, RW-21).
- **RW-22:** the running `assay-r2` on `62d9a66a` continues to its verdict;
  fix rounds do not run r2; the final r2 runs once on the reviewer-ACCEPTed
  tip and resumes from `.assay/mutation-state/` (content-keyed candidates).
- **RW-23:** reviewer decision asks ruled: RW-7 applies to all "last read"
  fields; the bare-host `assay-r2` `stall_timeout` is removed (inert);
  `RUN_GATE_PROFILE=""` = absent; the token is redacted in the header
  line; the basic path never fabricates `target.cgroup`; the canary copies
  without `.assay/`/`.run-gate/`/`coverage.json`; the pre-existing
  `coverage_gate` record-lookup false green is fixed if small, else RG-58;
  all listed doc drift fixed in the round.

- **P2 fix round 1 → review round 2 (tip `a7a84e09`): ACCEPT**, two
  records-only conditions: (1) the deferred residues (S1/D5 doctor warning
  for bare-host `stall_timeout`, S6, S11, S13-code, the S14 doc-drift list)
  become real backlog rows (RG-58..); (2) the B3/M5 claim is corrected — M5
  (`peak_over_baseline` floor at 0 in scope `container-shared`) is an
  EQUIVALENT mutant (the max over a list whose first element is the
  baseline cannot go negative; 340 combinations, zero negatives), so the
  LOG must say so instead of "proved the floor". Also fold at merge: SPEC
  `R-43f` must not claim `profile_token` is recorded for exec lanes (no
  recovery record exists there). Gates at the tip: selftest 1083 passed,
  873/873 lines, 334/334 branches; assay-r1 PASS; assay-r3 2 rejected /
  0 survived. RW-21 verified live (cpu.seconds 0.093 → 0.292 against the
  container's 0.321 s lifetime).
- **RW-24 (S11, byte medians):** byte-valued series in `history` stats and
  the footprint manifest use the nearest-rank p50 (an integer element of
  the series, consistent with §7), never the arithmetic midpoint (`100.5`
  bytes is not a measurement); float series (`duration_seconds`,
  `cpu_cores_avg`, stall seconds) keep `statistics.median` (R-36d
  unchanged). Lands in the P2 close-out with a test.
- **P2 close-out plan:** after session 7's r2 verdict + triage lands (it
  owns the worktree's LOG/REPORT until then), ONE fresh close-out
  implementer: ACCEPT conditions (backlog rows, B3/M5 correction, `R-43f`),
  RW-24, then the final `assay-r2` on the tip (resume, RW-22), final gates,
  return → merge `--no-ff`.

- **RW-25 (supersedes RW-20/RW-22 for the stale run):** the assay-r2 run
  on `62d9a66a` is STOPPED (65/256 after ~3 h; the tip diverged
  substantially in the fix round, so most remaining candidates would be
  re-judged anyway). Its partial survivor list is recorded. The final r2
  runs ONCE on the close-out tip with `jobs = 2` in run-gate-project's
  `assay.toml` (host: 8 cores, ≤ 2 gate containers still respected — the
  lane is bare-host; each candidate runs in its own assay scratch snapshot
  with its own pytest basetemp, so two candidates do not share state),
  resuming what `.assay/mutation-state/` still matches by content.
- P1 status: r2 on the pre-RW-21 tip → FAIL MUTANTS_SURVIVED, triage
  committed (`5058f04d`: 28 killed, 3 justified), `main` merged
  (`c97bd176`), RW-21 adoption in progress; then one r2 resume run, then
  the P1 reviewer.

## Dispatch

| package | worktree | branch | implementer | reviewer | status |
|---|---|---|---|---|---|
| P1 — cgroup-profiler daemon | `.worktrees/rg55-profiler-daemon` | `rg55-profiler-daemon` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | sessions 1–5 → C0–C9 + live acceptance (`8cdd09e6`, RW-19 `71c6f607`); r2 lane in flight (123/217 at 09:56); reviewer pending the r2 verdict |
| P2 — run-gate client | `.worktrees/rg55-run-gate-client` | `rg55-run-gate-client` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | sessions 1–7 → C1–C8 complete (`62d9a66a`, rev 41, footprint manifest from a live probe); assay-r2 in flight (14/256 at 09:56, RW-20); reviewer round 1 dispatched on `62d9a66a` |
