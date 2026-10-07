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

- **RW-26 (orphaned mutation container; untracked long lanes):** at 12:34Z
  the Claude Code low-memory guard killed both agents' tracked background
  commands ("stopped because the system is running low on memory"; host at
  ~860 MiB free with the dstdns stack and the game server resident). P2's
  `assay-r2` survived because it was launched `nohup … & disown`
  (untracked); P1's `./run-gate.py r2` OWNER (pid 2315801) died while its
  container `run-gate-vbpub-r2-2315801-…` kept judging (37/208 at 12:36Z,
  ~55 s/candidate, ETA ~15:15Z, inside RW-19). Ruling: an orphaned
  mutation container is left to FINISH (never `docker rm -f` a progressing
  run, never restart from scratch — `.assay/mutation-state/` is keyed by
  candidate content, nothing is lost); the agent then captures exit code +
  `docker logs` tail, removes the container, and RE-RUNS the lane as a
  resume under run-gate ownership so the R-36 history record and the
  run-gate verdict are real. From now on every long lane is launched
  untracked (`nohup … > log 2>&1 & disown`) and observed with a cheap
  tracked `until` loop that is re-armed if killed; no additional pytest or
  container may start while a mutation run is live on this host. P1 was
  told to commit its pending working-tree edits (a `manifest["duration"]`
  assertion found while waiting, LOG/REPORT) and write BRIEF-6 now as
  insurance (its context is ~513k). P2's run: 283 candidates, `jobs = 2`,
  started 12:27Z on `186461de` (close-out commits `026663c1`, `cc19e1f0`,
  `82094849`, `69d46544`, `186461de`: RW-24, RG-58..RG-61, B3/M5, R-43f,
  jobs). Both agents' watchers are exposed to the same guard; the
  controller's heartbeat checks both runs and messages an idle agent
  whose run has finished.

- **RW-27 (third track: the wave's own backlog entries are folded in):**
  operator 2026-09-12 ~12:55Z: "can you start work on new backlog entries
  you filed as well and fold them in? you are free to run a 3rd track in
  parallel". Ruling: the base packages ship first, unchanged (run-gate
  23.7.0 rev 41, cgprofile 1.0.0 — their reviews are done or imminent and
  are not reopened); the follow-ups ship as SECOND releases in the same
  wave (run-gate 23.8.0 rev 42, cgprofile 1.1.0), each with its own fresh
  adversarial review and full assay lanes. Packages: **P4** run-gate
  follow-ups RG-57, RG-58, RG-59, RG-60, RG-61 on branch
  `rg55-followups-run-gate` from the P2 tip `186461de` (dispatched NOW;
  handoff `run-gate-WAVE-RG55-P4-HANDOFF.md`, review handoff written);
  **P6** cgroup-profiler follow-ups CP-4 (id-suffix flake), CP-5
  (events.jsonl records), CP-6 (DAMON series in `ctl report`), CP-7
  (limits table resolved) from the P1 tip, dispatched when P1's r2
  container exits (2-container cap + memory); CP-1 (retention tuning
  needs real data), CP-2 (socket transport) and CP-3 (DAMON paddr) stay
  OPEN — they need measurements or a design round, not this wave; **P5**
  RG-56 admission after BOTH base merges: contract amendment by the
  controller first (`ctl status` must expose each session's
  `meta.expected`; run-gate computes go/wait/refuse client-side from
  `status` + `host` PSI + the manifest, `--allow-pressure`, `--dry-run`
  reports), then one daemon-side and one run-gate-side package.
  Sub-rulings: **RW-27a** RG-58 = option 2 (load-time WARNING + `doctor`
  WARN, never refuse); **RW-27b** RG-57 = both halves as filed (daemon
  path via self container id + token, `scope container-shared`;
  daemon-absent `method: "rusage"`, `source: "rusage-maxrss"`, no basic
  sampler on that path); rusage entries are footprint-eligible with the
  source caveat printed. Host rule for the third track while two
  mutation runs are live: targeted pytest only until P2's `assay-r2` pid
  exits; one mutation run per project at a time.

- **RW-28 (hung mutation candidate; `budget_per_candidate` mandatory):**
  P1's r2 (orphaned container, RW-26) stalled 37 min on candidate 47
  (`lib/serve.py:479`, `True->False`: the session-loop
  `threading.Thread(daemon=True)` flipped to non-daemon → pytest finishes
  its tests and hangs at interpreter exit on the thread join; futex wait,
  0 % CPU). cgprofile's `assay.toml` r2 lane set no `budget_per_candidate`
  (assay B012's optional key), so assay waited indefinitely — and with the
  run-gate owner dead (RW-26) nothing enforced the 4 h lane budget either.
  13:22Z: the controller SIGKILLed that pytest inside the container; the
  run resumed at once (candidate id `30262744b91e83a5…`, whatever assay
  recorded for it is not an honest verdict). Ruling: (1) every r2 lane in
  this wave sets `budget_per_candidate` (cgprofile `600s`, run-gate `900s`
  via P4); (2) the mutant is killed HONESTLY by a test asserting the
  thread is a daemon thread, never by a hang or by the kill; (3) before
  the resume under run-gate ownership the agent deletes that candidate's
  `.assay/mutation-state/<id>.json` so it is re-judged — assay B088:
  `--resume` keys candidate identity on the mutant's source bytes, not the
  test suite, so a test-only fix would replay the stale verdict; (4) the
  tool finding (no default per-candidate budget → one hung mutant blocks
  the whole run; nothing warns when the key is unset) is filed in assay's
  backlog per the cross-repo convention. Both agents told (P1 addendum,
  P4 addendum).

- **RW-29 (liveness / placement / admission design adopted; tracks P5–P8):**
  operator ~13:40Z: fixed budgets are ceilings, not detectors, and fail on
  old hardware; judge progress mechanically; consider a slice with
  guarantees, the daemon's own slice, lanes naming what they need, the
  root daemon adjusting cgroups; "write up a design doc and example flow
  … persist our reasoning … fold this into our planned work … another
  parallel track … RAM PSI is the only limit". Ruling: the design of record
  is `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-
  admission.md` (D-17..D-26: bound absence of progress; daemon = liveness
  oracle + actuator in a new `dev-infra.slice`; `dev-gates.slice` as the
  capacity object; lanes name requests, the daemon places exec/bare-host
  lanes into `rg-<token>` leaves with `memory.high` throttling; detached
  run-gate owner + attach; PSI-paused stall clock with named verdicts;
  assay auto bound + cadence hints + `os._exit` runner + `hung` +
  `--rejudge`; env-unset fallback so nothing breaks before the host is
  updated; D-15 whitelist extension; ciu v8 D.7). Packages: **P7** assay
  B091 (dispatched now, `assay-liveness`), **P8** mdt host-setup slices
  (dispatched now, `mdt-dev-slices`), **P6** cgprofile CP-4..7 then CP-8/
  CP-9 after the v1.1 contract amendment (controller-authored), **P5**
  run-gate RG-56 + RG-62 after P4 and P6 merge. Releases: assay 6.2.0,
  cgprofile 1.1.0, run-gate 23.9.0 (after 23.8.0 from P4). Host rule for
  the parallel tracks: RAM PSI is the limit (back off while memory `full
  avg10 > 5`), one mutation run per project, ≤ 2 gate containers. Backlog
  rows RG-62 and CP-8/CP-9 are filed by P5/P6 from their handoffs (the
  run-gate backlog file is under edit on two unmerged branches; appending
  on main now would conflict).

- **RW-30 (boundaries corrected — design amendment A1, D-27..D-29):**
  operator ~14:25Z: mdt is a devcontainer/cockpit template whose `dev*`
  slices CONTAIN dev load; the root daemon is a host DEPLOYMENT consumed
  by run-gate / ciu gate from devcontainers; the watcher should be a
  service with the daemon. Ruling: (1) the daemon is the singleton
  watcher — run-gate authors the stall policy at `ctl start`, the daemon
  judges liveness + cadence and enforces with `cgroup.kill`, records the
  verdict; the run-gate client is disposable (D-21 detached owner
  DROPPED); (2) `dev-infra.slice` and `CGROUP_PARENT_DEV_INFRA` are
  WITHDRAWN — the daemon ships its own top-level `cgprofile.slice` with
  its deployment (P6), authored `cgroup_parent`, implicit unbounded slice
  when the unit is not installed (reported by `ctl host`/`doctor`);
  (3) `dev-gates.slice` stays in mdt (gates are dev load; capacity
  object). P8 re-scoped by message (dev-gates only; drop the dev-infra
  unit it already committed in M1); P6/P5 handoffs will carry D-27..D-29;
  SPEC-V8 D.7 item (5) corrected.

- **RW-31 (transport: exec and socket as interchangeable carriers, D-30):**
  operator ~14:50Z after the controller's transport assessment (exec today
  = 4 docker API calls + a python spawn per verb, 100–400 ms idle, seconds
  under load, one exec per 30 s status poll; mounted Unix socket = same
  protocol, no spawn, push-capable, needs a devcontainer mount; TCP
  rejected — privileged daemon, `network_mode: none`, no multi-host need):
  "make `docker exec` and the socket fully interchangeable, both offering
  the full functionality … build the socket in parallel and ship as well so
  I can switch directly later". Ruling: design amendment A2 / D-30 — one
  listener, two carriers, identical verbs/responses/errors, `watch`
  streaming on both, docker-group trust boundary on the socket + optional
  uid allowlist, client `transport = auto|exec|socket` with `doctor` dual
  probe; exec stays the default and a permanent fallback; only the
  template mount needs a rebuild (P8 M5 after review); parity proven by a
  probe container before any rebuild. Scope lands in P6 (daemon), P8 M5
  (mount), P5 (client).

- **RW-32 (P8 review round 1 — ACCEPT-conditional B1–B6; decision asks D1–D5
  ruled; M5 folded into round 2):** round file
  `run-gate-WAVE-RG55-P8-REVIEW-round1.md` (~14:50Z). Rulings: **D1** the
  cap watcher gains `dev-gates.slice` with its own knob
  `DEV_CAP_GATES_MEMORY_MAX`, default `4G` (= the tier's `MemoryHigh`: one
  lane alone can drive the tier into throttle but never past `MemoryMax`;
  two lanes are bounded by the tier's oomd pressure kill at 6G; `1G` would
  re-create the incident this wave fixes). Finer per-lane caps come from
  daemon placement (D-25, P6/P5) and COMPOSE with the watcher's coarse
  backstop — the watcher is not withdrawn when placement lands. **D2**
  `ManagedOOMSwap=kill` is DROPPED: D-19 lists only the pressure kill and
  `MemorySwapMax=32G` makes swap the gates' relief valve — an oomd swap kill
  contradicts the design. **D3** `CPUWeight=20`/`IOWeight=10` STAND (D-19);
  the README states the arithmetic (interactive's worst-case share under
  3-way contention 83% → 71%, accepted because gates and background are
  rarely both busy; revisit on a measurement, not a guess). **D4** mdt
  `AGENTS.md` is updated NOW (P8 scope); run-gate/cmru/srdm consumers are
  propagated at P5 (run-gate's default parent) and their rows filed in
  their own backlogs by the controller after merge — the P8 REPORT lists
  them under "onward propagation". **D5** P8 never touches mdt `TODO.md`
  (dirty in the shared checkout — a merge touching it would abort); the
  record goes to `host-setup/README.md` ("Changes") + the P8 REPORT, and the
  operator adds the TODO line themselves. B1–B6 accepted as prescribed; the
  wizard earmark-sum defect (fourth `MemoryHigh` missing) is promoted to
  REQUIRED (cheap, real); `AGENTS.md` variables, REPORT `Tip:`, the
  "byte-for-byte" overclaim and the D-24 fallback wording are fixed in the
  same pass. **M5 (D-30 template mount)** lands in the SAME repair set so
  round 2 covers it: `templates/devcontainer.json` bind-mounts
  `/run/cgprofile` (the `--group-add ${localEnv:DOCKER_GID}` already
  present is exactly the socket's group — no new gid plumbing); because
  `mounts` entries are `--mount` (docker refuses a missing bind source),
  host-setup ships a `tmpfiles.d` entry `d /run/cgprofile 0770 root docker -`
  installed + applied by `install.sh`, verified by `check.sh` (dir mode +
  owner; socket present → INFO "socket carrier available", absent → INFO
  "exec carrier only"); the daemon (P6) re-asserts owner/mode at start as
  belt and braces. Round 3 stays in reserve.

- **RW-33 (P7 checkpoint BRIEF-1 — liveness mechanism decided for A2–A4;
  fresh successor dispatched):** P7 session 1 shipped A1 (`de32bb91`:
  `budget_per_candidate = "auto"` default, `"none"` opt-out, `plan`
  progress event, `judgment.r2.budget_per_candidate_derived_s`) and cut at
  `723c431d` with the architectural findings: assay runs every command via
  the blocking `default_process_runner` on the lane's own argv; baseline
  and candidates share one `CommandPlan`; the candidate subprocess runs in
  the PROJECT's interpreter (assay is often a pyz, not importable there).
  Ruling — ONE mechanism, two parts, R2 candidates only:
  (1) **materialized plugin**: assay writes a stdlib-only pytest plugin
  file into `.assay/liveness/` (content-hashed) and injects it into the
  shared plan via `-p <module>` + `PYTHONPATH` prepend ONLY when the lane
  argv literally invokes pytest (`pytest`, `…/pytest`, `-m pytest`);
  otherwise liveness is off with one WARN and D-23 budgets remain the only
  bound (documented requirement). The plugin emits `test` events (nodeid,
  outcome, duration) and a `session_finish` event (exitstatus) to a side
  file named by `ASSAY_LIVENESS_EVENTS`, and calls `os._exit(exitstatus)`
  from `pytest_unconfigure(trylast)` — after the terminal summary, after
  pytest-cov's sessionfinish write — ONLY when `ASSAY_LIVENESS_EXIT=1`,
  which assay sets for candidates and never for R0/R1 (coverage atexit
  writers stay intact). (2) **`LivenessRunner`** replaces the runner for
  the R2 candidate path only (`_execute_mutation_jobs._run_one`):
  `Popen(start_new_session=True)`, stdio to files, 1 s loop; `hung` when
  no `test` event (or, plugin-less, no stdout growth) for
  `expect_next_event_within_s = max(3 × slowest_test_s, 15 s)` AND the
  process tree's CPU time grew < 1 s over the last 30 s, or when
  `session_finish` was seen and the process is still alive 30 s later →
  `killpg(SIGKILL)`; CPU-spinning mutants are NOT hung — they hit the
  budget ceiling (`budget_exceeded`). `hung` is a new bucket scored like
  `budget_exceeded` (outside the killed/(killed+survived) denominator),
  reported separately, additive schema (no v12 cut). `test` events reach
  the progress stream for the BASELINE only; candidate events gain
  `tests_completed`; the `plan` event gains `slowest_test_s` +
  `expect_next_event_within_s` (A4). A5 `--rejudge` per the handoff sketch.
  Successor: fresh Sonnet seeded with BRIEF-1; the checkpoint clause is
  HARD this time (session 1 ran 370 calls before cutting).

- **RW-34 (contract v1.1 landed — §8 of `RG55-INTERFACE-CONTRACT.md`,
  mirrored to `scripts/cgroup-profiler/docs/`):** additive under
  `contract: 1`: §8.1 two carriers (socket request line
  `{"verb","args","contract"}`, `peer-refused`, `auto|exec|socket`
  semantics, exec permanent); §8.2 `ctl watch` NDJSON (`reading` every
  `--watch-interval` 30 s [5, 300], `verdict` on state change, one `end`;
  consumer idle timeout 3 × interval + re-attach); §8.3 placement
  (`--place --memory-high --memory-max --cpu-weight`, `placement
  {requested, leaf, applied (read back), pids_moved, error}`, never fails
  `start`, D-25 whitelist enumerated incl. `cgroup.kill` + `rmdir` on
  `rg-*` only); §8.4 liveness block + policy options
  (`--progress-stream`, `--idle-bound auto|s` = max(300, 3 × cadence
  hint) with a PSI-paused clock, `--ceiling auto|s`, `--on-stall
  kill|report`, default `report`) and the state vocabulary ok / stalled /
  hung / runaway / throttled / over_ceiling; §8.5 `host.gates_slice` +
  `daemon_slice`; §8.6 `version.transports`; §8.7 Summary `liveness` /
  `placement` / `watch`; §8.8 error codes; §8.9 consumer obligations for
  P5. Goldens: P6 adds one fixture per new shape, P5 verifies bytes; v1
  goldens stay byte-identical. Design doc §5 now points here.

- **RW-35 (P6 dispatched now; three settled details):** P6 (cgprofile
  follow-ups, handoff `scripts/cgroup-profiler/nyxloom-trove/reports/
  cgprofile-P6-FOLLOWUPS-HANDOFF.md`) starts from the P1 branch tip
  before P1 merges (targeted pytest only while the two mutation runs live;
  merges `rg55-profiler-daemon`/`main` when told). (a) D-25 whitelist
  gains ONE non-leaf write: `+memory +cpu +pids` into the gates slice's
  `cgroup.subtree_control` (never `-`), because a manually created leaf
  cannot take `memory.high` unless its parent delegates the controller.
  (b) Socket group = the mounted directory's gid (the host `tmpfiles.d`
  entry is the source of truth; a root:root directory means root-only
  socket until host-setup is installed — exec unaffected). (c) The
  singleton `cgprofile-host-daemon` is the controller's; P6 probes with
  its own `cgprofile-p6-probe` instance on a scratch `/tmp/cgprofile-p6`
  mount (CIU-104 name-collision lesson applied). The socket carrier is
  backlog row CP-2 (already filed by P0); CP-8 watch and CP-9 placement
  are filed by P6.

- **RW-36 (P7 session 2 flag — liveness injection vs A-036 "flags never
  derived by assay"):** session 2 (`ef5088f6`) shipped the materialized
  plugin, the pytest-argv rule and a v1 `LivenessRunner` (still blocking),
  and flagged that the injection extends `CommandPlan.argv_declared`
  directly, bypassing the `allow_argv_append` consent gate. Ruling: the
  plugin is judge mechanics (observation + exit hygiene; it changes no
  test selection or behaviour), so it is NOT the A-036 case — but the
  lane's declaration must stay the lane's own words: liveness argv goes
  through `argv_appended` (the channel R1 coverage flags use), never
  `argv_declared`; it is gated by a new `judge.mutation.liveness =
  "auto" | true | false` (default `auto` = on when the lane argv invokes
  pytest; `true` on a non-pytest argv refuses at load; `false` = off, D-23
  budget only) — NOT by `allow_argv_append`, which keeps its coverage-flag
  meaning; the `plan` progress event and the verdict's `judgment.r2` gain
  `liveness: {active, reason, plugin}` so a reader sees what ran. Session
  3 is a fresh successor from BRIEF-2 with this ruling; A3's active
  monitoring loop (Popen + /proc tree CPU + side-file cadence + killpg)
  is the next deliverable, exactly as RW-33 specifies.

- **RW-37 (P8 round 2 — B1–B6 + M5 PASS; new B7/B8; round 3 = last):**
  round file `run-gate-WAVE-RG55-P8-REVIEW-round2.md` (~15:45Z). B7:
  `check.sh` `_bytes_of` breaks on legal systemd sizes (`4.5G`, `50%`) —
  the wizard itself emits half-gigabyte values; fix = awk parser, `?` →
  warn never fail. B8: the README's new "docker run fails outright on a
  missing cgroup parent" claim contradicts four verified fail-OPEN
  statements in the repo — replaced by "caught by `check.sh`, run-gate
  `doctor`, `ctl host`". Accepted S-items: tmpfiles entry renamed
  `mdt-cgprofile.conf` (the daemon package never writes tmpfiles; the
  name is mdt's), full-contention share figure incl. buildkitd +
  guaranteed tiers, REPORT tip/labels, non-vacuous assertions (7)/(8),
  and the MERGE-ORDERING constraint recorded: host-setup must be
  installed on the host BEFORE any devcontainer is rebuilt from the
  template (the `/run/cgprofile` `--mount` refuses a missing source) —
  the operator sequence says so.

- **RW-38 (P8 MERGED `a71c46b0`, review round 3 ACCEPT at `e326cc9b`):**
  mdt host-setup now ships `dev-gates.slice` (D-19 sizing, no
  `ManagedOOMSwap`), the cap watcher's `DEV_CAP_GATES_MEMORY_MAX` (4G),
  `check.sh` size-aware comparison, `install.sh` missing-keys WARN, the
  `/run/cgprofile` template mount and `mdt-cgprofile.conf` (tmpfiles.d,
  mdt-owned — P6 ships none). Residual R1 (a typo'd size warns instead of
  failing) is an mdt follow-up line for the operator's TODO.md (dirty in
  the shared checkout; the REPORT carries the paste-in text). OPERATOR
  ACTIONS (not automatable from here): install host-setup on the host
  BEFORE any devcontainer rebuild from the template, per the sequence in
  `run-gate-WAVE-RG55-P8-REPORT.md`; expect `mdt-host-check.sh` OK/OK on
  memory.max/high and `/run/cgprofile` 0770 root:docker; a rebuilt
  devcontainer then carries the socket mount. Worktree
  `.worktrees/mdt-dev-slices` removed; branch kept until the wave closes.

- **RW-39 (gate rule relaxed for non-mutation lanes — PSI is the
  signal):** the handoffs' "no run-gate lane while a mutation run is live"
  was capacity caution; the operator's standing rule is that RAM PSI is
  the only limit. Ruling: r0/r1-style pytest lanes and r3 canaries (bare-
  host, serial) MAY run while other mutation runs are live, one lane at a
  time, `nice -n 19 ionice -c 3`, launched only while memory `full avg10`
  < 5. Still serialized estate-wide: r2 mutation lanes (one per project,
  ≤ 2 gate containers) and image builds/probe containers. Applies to P6
  session 5 onward and to P7's next session; P4 keeps its handoff's
  sequencing (its whole-suite run waits for pid 2415767 because it merges
  P2 first). Also noted: P1's "resume" under run-gate ownership re-judges
  all 208 candidates (the container snapshot does not carry the untracked
  `.assay/mutation-state/`), ~3.5 h instead of minutes — accepted, the
  verdict must be on the final tree anyway; P1 records the mechanism.

- **RW-40 (P2 assay-r2 hit its 4 h LANE budget — resume, no re-run, no
  budget change):** 16:28Z, `BUDGET_EXCEEDED/LANE_TIMEOUT (exit 4)` after
  14401 s with ~165/283 candidates judged; the host carried two mutation
  runs and the bare-host lane runs ~75–90 s per candidate here. This is
  the design's own incident class (a ceiling killing progressing work —
  §1 of the design doc gains it as row 4 at close-out). Ruling: P2 relaunches
  the same lane untracked; assay's mutation state in the worktree
  (`.assay/mutation-state/`, never in a snapshot for a bare-host lane)
  makes it a resume; if the resume does not skip judged candidates P2
  stops and reports. Budgets stay as they are; the reviewer's ACCEPT on
  `186461de` stands (no code change). P4 is woken under RW-39 for
  selftest/r1/r3 now, r2 only after P2's resume finishes (one mutation run
  per project). Monitor `bvlfxll3r` ended; the P1 resume container is
  watched by `b5fc67sgd`.

- **RW-41 (P2 resume rejected every record — per-tree identity; re-run
  at the original tree; assay B092 filed):** the 16:31Z relaunch printed
  `resume: rejected_total=174, resumed_total=0` — assay 6.1.1's B088 fix
  keys resume on the WHOLE judged tree, and the only commit between the
  two runs (`647a2cc6`) added `run-gate-WAVE-RG55-P2-LOG.md` under the
  project's own `nyxloom-trove/reports/`; a records-only commit
  invalidated 4 h of judging. run-gate's lane `budget` is advisory; the
  enforcing one is assay's `budget_s`, which is itself in the tree, so a
  budget bump would also invalidate. Ruling: P2 runs the r2 lane with the
  worktree detached at `186461de` (the tree the 174 records were judged
  on) so 109 candidates remain; started now despite P1's concurrent
  container (worst case a short third resume at the same tree); after
  the verdict the worktree returns to the branch. Post-triage rule: if
  survivor triage adds tests, the final r2 re-executes every candidate
  (B088's correct semantics) — once, alone on the host; equivalent-mutant
  justifications alone need no re-run. **B092 (assay, filed via P7):**
  resume identity must exclude non-judged paths (records, docs, the
  `nyxloom-trove/` tree) or honour an ignore list — a LOG commit must
  not cost a mutation run. Also noted: P1's container "resume" re-ran
  all 208 for the same reason (`5ce232d1` changed `assay.toml`).

- **RW-42 (mutation-run concurrency — up to two estate-wide, PSI-gated;
  P4 review starts before its r2):** the "one mutation run per project"
  phrasing was load caution; with per-tree resume (RW-41) two concurrent
  runs cost per-run wall time, not throughput, and the host's RAM PSI is
  the only limit the operator set. Ruling: ≤ 2 mutation runs estate-wide
  regardless of project, launched only while memory `full avg10` < 5;
  budget hits are answered by a same-tree resume, never by editing the
  tree. P4's r2 starts when a slot frees (P1's container, ~19:30Z), from
  BRIEF-3, as a fresh session. To shorten the critical path the P4
  adversarial review (fresh Opus) starts NOW on `0bb3bbeb` with selftest
  / r1 / r3 green; its ACCEPT is explicitly "pending the r2 survivor
  table", which arrives as a fix-verification round. P4 session 3 also
  landed the wave goal `run-gate-project/run-gate.footprint.json`
  (`02707e30`) from a real `footprint --write`.

- **RW-43 (P4 review round 1 — ACCEPT-conditional B1–B4; rusage must
  come from `os.wait4`; contract §4.3a):** round file
  `run-gate-WAVE-RG55-P4-REVIEW-round1.md` (~17:40Z). B1 is real:
  `getrusage(RUSAGE_CHILDREN).ru_maxrss` is a high-water mark over every
  reaped child, so a `["true"]` lane recorded 36 MiB (run-gate's own
  docker/git children) into history, the tracked manifest and
  `meta.expected`. Ruling: the rusage path takes the LANE's own numbers
  from `os.wait4(pid, 0)` (Popen + wait4, `returncode` via
  `os.waitstatus_to_exitcode`) — exact, no baseline arithmetic, no null
  for light lanes (D2 moot); oracles pin `ru_maxrss × 1024`, `utime +
  stime`, `cores_avg` (B2). B3: the exec-lane inflight record is stamped
  `"runner": "exec"` and the container path REFUSES a foreign record
  (never `docker rm -f` a container run-gate did not create — CIU-104
  class). B4: `doctor` wording. Non-blocking accepted: rusage caveat on
  the live `footprint` line and `history`; RG-59 match narrowed to
  docker's own exec failure (exit status + stderr prefix), never the
  daemon's stderr; the circular RG-60 test; stale comment; RG-61 counts.
  D1: contract §4.3a added (this ruling) and mirrored. Repairs by a
  FRESH P4 session 4 from the round file; reviewer round 2 on the
  repair tip; r2 still pending a slot (RW-42).

- **RW-44 (P6 C8 placement landed `a654bd5d`; session-6 decision asks
  accepted; CP-10 test isolation; CP-11 orphaned leaf):** all nine
  session-6 defaults stand — a malformed cap value is `bad-argument`
  (exit 2, no session) while host conditions are `place-refused:*`;
  `placement` is `null` only when `--place` was never requested;
  `applied` holds only the requested caps; a stop-time `rmdir` failure
  keeps `leaf` non-null (honest); `rmdir` is an injectable seam (kernfs vs
  tmpdir); `parent-not-gates-slice` refusal added; D-25 rows in
  `events.jsonl` per cgroup write. The r0/r1 lane is currently order-
  dependent (C7's `TestPeerCredentials` leaks state into
  `TestRealSubtreeEnforcement`; pre-C8, bisected) → CP-10, root-cause fix
  in session 7 before C9; `gc` not reclaiming a leaf orphaned by a daemon
  restart → CP-11 (open, next release). Session 7 (Sonnet) dispatched for
  CP-10 + C9 close-out + gates; probes still wait for a mutation-free
  window (RW-42).

- **RW-45 (P6 C9 complete `241122b6`; image builds under PSI; frozen
  goldens' version string):** C1–C9 are on the branch (r0/r1 GREEN 1335
  tests 100%/100%, r3 GREEN); r2 and the live probes wait for a mutation
  slot (RW-42) and a build. Ruling: (a) image builds and probe containers
  are allowed whenever memory `full avg10` < 5, one build at a time, the
  probe container counting toward the ≤ 2 gate-container cap — the earlier
  "mutation-free window" wording is withdrawn; (b) the frozen cross-package
  goldens `run-gate-project/nyxloom-trove/fixtures/rg55/*-v1.json` may
  change ONLY in the producer version string (`1.0.0` → `1.1.0`) — contract
  §6 identity binds both copies, so the string tracks the producer's
  release; the P6 reviewer runs run-gate's fixture-reading tests against
  the branch's fixtures and P5 re-asserts bytes; (c) CP-10's root cause
  is the host-PSI seam (`host_proc_root`), not a peer-cred leak — the
  reviewer re-checks both hypotheses. P6 session 8 (r2 + probes) is
  dispatched when P1's container frees a slot; review handoff written.

- **RW-46 (P4 session 4 — B1–B4 repaired `00a79de4`; selftest red on a
  host-wide lock hazard; rusage floor):** (a) run-gate's test suite
  touches the production `SHARED_LOCK_DIR = "/tmp"` (R-41 exec mutex),
  so concurrent worktrees' pytest runs contend and 493 stale
  `run-gate-exec-*-runner.lock` DIRECTORIES (never a legitimate shape)
  accumulated since Sep 3. Ruling: production semantics stay (the mutex
  is per host by design); the TEST SUITE isolates the lock dir with an
  autouse fixture (tmp-scoped), and the code path that can create a lock
  as a directory is root-caused and fixed with a regression test; `doctor`
  gains an INFO/WARN for stale lock entries older than a day (report only,
  never delete). (b) A short-lived bare-host lane's `ru_maxrss` is bounded
  below by run-gate's own RSS at spawn (fork/CoW accounting) — inherent;
  ruling: the rusage summary gains `memory.floor_bytes` (run-gate's RSS
  read from `/proc/self/statm` at spawn) and consumers treat a peak ≤
  floor as "unmeasurable, at most floor"; `footprint`/`doctor`/SPEC
  R-43i say so; the manifest marks such lanes `"peak_at_floor": true`
  and RG-56 admission treats them as small. Session 5 (fresh) lands (a),
  (b), S1–S5, regenerates the footprint manifest + CONSUMERS transcript,
  and returns with selftest/r1/r3 green; reviewer round 2 follows on that
  tip; r2 when a slot frees.

- **RW-47 (controller incident 19:47Z — P1's relaunched r2 container
  destroyed by an image-filtered sweep):** after stopping a duplicate P7
  gate run the controller removed containers by `--filter
  ancestor=tester-unified:local`; that image is shared by run-gate lane
  containers, so P1's `run-gate-vbpub-r2-3431654-…` (relaunch at
  `5ce232d1`, in its baseline phase) was removed too. Records at that
  tree are intact; P1 relaunches once more (≈ 15 min). Rule (memory
  `docker-remove-by-exact-name-only`): containers are removed only by the
  exact name of the job that created them — never by image, label or
  prune while other sessions run. The duplicate P7 gate run itself was
  stopped correctly (the registered gate was already green on the same
  tip; a second run gained nothing and risked the container cap).

- **RW-48 (P1 r2 `BUDGET_EXCEEDED` is the hung mutant, not a lane
  budget — root fix, then one full run; P6 waits for it):** the resumed
  run (207 records accepted at 19:50Z) judged the last candidate and still
  ended `BUDGET_EXCEEDED/LANE_TIMEOUT` at 20:00Z. In assay 6.1.1 any
  candidate in the `budget_exceeded` bucket makes the lane outcome
  `BUDGET_EXCEEDED` (mutation.py ~3033/3123; B090's "a hung mutant blocks
  the whole R2 run"). The candidate is RW-28's `lib/serve.py:479`
  `daemon=True → False`: the daemon assertion FAILS the test, but the
  mutated non-daemon sampler thread blocks interpreter shutdown, so pytest
  never exits and the 600 s per-candidate budget classifies it
  `budget_exceeded` instead of `killed`. Ruling: root fix in P1 — every
  test that starts the sampler thread stops it at teardown
  (shutdown + join), plus a session-end safety net asserting no live
  non-daemon threads; prove by applying the mutant by hand and watching
  pytest exit non-zero within seconds; triage the other survivors now;
  then ONE full r2 run (the tree changes; ~2.5–3.5 h). P6's r2 is HELD
  until it merges that fix (its branch carries the same site). Lesson for
  the design record: a "hang at exit" mutant is exactly the class D-23 /
  P7's `os._exit` plugin removes at the root; until assay 6.2.0 ships,
  projects must not leave non-daemon threads alive at test teardown.
  RG-62 is now P4's flaky-test row; P5's row is RG-63.

- **RW-49 (P7 review round 1 — REJECT B1–B5; rulings D1–D3):** round
  file `run-gate-WAVE-RG55-P7-REVIEW-round1.md` (~20:20Z), every blocker
  reproduced end-to-end. B1: the plugin's `os._exit(0)` default turns a
  session that never started (a raising `conftest`) into a false SURVIVOR
  — fix: `_EXIT_STATUS = None` sentinel, `pytest_unconfigure` returns
  instead of exiting when unassigned. B2: the idle bound is derived from
  `call` durations only, so a slow fixture/collection makes healthy
  candidates `hung` at the 15 s floor — **D3 ruling: calibration (a)** —
  the plugin records `session_start` (`pytest_configure`), every phase
  (`setup`/`call`/`teardown`, `when` on the record) and `session_finish`;
  `expect_next_event_within_s = max(3 × the baseline's worst observed
  inter-event gap incl. the leading and trailing gaps, 15 s)`; the
  pre-first-event bound is 3 × the baseline's leading gap; only `call`
  events are forwarded to the progress stream (A4 contract unchanged);
  the plugin-inactive fallback stays. B3: writer and reader of
  `candidate.tests_completed` both key on `run_cwd`. B4/**D1: keep
  `schema_version` 11** and DISCLOSE (BREAKING in CHANGES, CONSUMERS
  migration note naming the exact `unknown mutation field(s): ['hung']`
  diagnostic; an assay < 6.2.0 `verify` refuses 6.2.0 native-R2 verdicts;
  consumers verify with the release that produced the document or newer)
  — the estate pins per consumer; a v12 cut is reserved for the next
  shape change. **D2: `judge.mutation.liveness` stays `"auto"`** because
  B1 and B2 are repaired in this package. B5: CHANGES restructured
  (Added/Changed/Fixed + BREAKING notes: bounded default budget,
  `unbounded` admission change, `argv_effective` moves, B4). S1–S10 folded
  where cheap, the rest listed as deferred with reasons. Repairs by a
  FRESH session 9; the gate re-run must not overlap any commit (HEAD
  movement voids the measurement); reviewer round 2 on the repair tip.
  Housekeeping: the controller's 19:23Z green run is in `history.json`
  (`exit_code: 0`, 1343.5 s) — its log was overwritten by the duplicate
  launch, so the "4686 passed" figure is from the voided run.

- **RW-50 (P2 r2: controller terminated one CPU-bound runaway candidate
  at 20:22Z):** candidate 105 (`run-gate.py:128`, `Eq->NotEq`) ran pytest
  at ~83 % CPU for 30 min with no end in sight; the `186461de` tree has NO
  `budget_per_candidate` (that key arrives with P4's C5 on the follow-up
  branch), so only the 4 h lane budget would have stopped it — at 21:02Z,
  voiding the whole run again (RW-40). Ruling: the controller sent
  SIGKILL to that candidate's pytest process only (pid 3534322); assay
  recorded the candidate at 1844.6 s and proceeded (jobs = 2 resumed
  immediately). The intervention is disclosed here and in P2's REPORT;
  at triage P2 states how assay classified candidate 105 (signal death →
  `killed` in 6.1.1's classifier is the expected reading — a never-
  terminating mutant IS detected; if assay put it elsewhere, it is
  triaged like any survivor). Lesson (already ruled RW-28): every r2
  lane sets `budget_per_candidate`; P2's base tree predates the ruling
  and cannot be edited without invalidating its records (RW-41); the
  follow-up release (P4, 23.8.0) carries the key.

- **RW-51 (P4 review round 2 — ACCEPT-conditional; only B5 open;
  r2 still pending a slot):** B1–B4, S1–S5, RW-46a/b all PASS with the
  reviewer's own probes; all three gates reproduce (selftest 1066/1066
  lines, 394/394 branches). B5: contract §4.3a (RW-43) says
  `meta.expected` carries `source` for run-gate ≥ 23.8.0 and the code
  still returns four keys — ruling: P4 adds the key + assertion (the
  clause stands; it is this release's). Non-blocking folded into the same
  session: regenerate the manifest/CONSUMERS transcript once more after
  the fixes (the committed one is a generation stale); assert the three
  behaviour-correct-but-unasserted mutants (exec `runner` stamp value,
  the footprint `[source:…]` note, history `any→all`); scope the
  never-touches-host-tmp test to the isolated lock dir (it flaked on a
  concurrent writer); set `Popen.returncode` after `wait4`
  (ResourceWarning); RG-59's 126/127 arm wording. RG-62's filing is
  honest (both flakes reproduced; wording nit fixed). P4's r2 waits for a
  mutation slot (P1 until ~23:30Z, P6 takes P2's slot ~21:00Z) — round 3
  = the r2 survivor table + B5.

### RW-52 — 2026-09-12 21:05Z — slot rule amended: bare niced runs are
PSI-gated, not slot-counted (RW-42 refined)

- Observed at 21:02Z: P2's 127-record resume finished; 2 candidates were
  still unjudged (`budget_exceeded` placeholders from the very first run,
  never executed), so P2 relaunched a short `--resume` pass (pid
  `4137438`, bare, jobs=2, nice 19/ionice idle) at 21:02Z. In the same
  minute P6 — told to take P2's slot once pid `1499375` was gone — launched
  its r2 (pid `4136306`, container `run-gate-vbpub-r2-4136306-…`). With
  P1's full r2 (container `…-3677631-…`, since 20:20Z) that is THREE
  mutation runs at once; memory PSI `full avg10=0.03`, load 12.5/8 cores,
  both containers `--cpus=3`.
- Ruling: RW-42's "≤ 2 estate-wide" counts CONTAINER runs (`docker`
  lanes at normal CPU weight — the host's `dev-gates.slice` is not
  installed yet, so nothing else bounds them). Bare `nice -n 19 / ionice
  -c 3` runs only take idle CPU and are gated by memory PSI alone (the
  operator's stated limit: launch nothing while `full avg10 > 5`). So
  P2's short pass stays; P4 may launch its bare assay-r2 as soon as P2's
  pass has exited (`pgrep -f 'assay-6.1.1.pyz run r2'` empty in the
  `rg55-run-gate-client` tree) and PSI is under 5, without waiting for
  P1/P6's containers (~01:30Z). Container-lane runs (P1, P6, later P7's
  gate) stay at ≤ 2.
- Why not wait: P4's merge already waits on P2's release (RW-27
  ordering); a further 4 h of idle serialization buys nothing the PSI
  gate does not already protect.

### RW-53 — 2026-09-12 21:10Z — P7 session 10 returned gate-unverified;
controller runs the gate as a third container (RW-39); round 2 dispatched

- P7 session 10 (Opus) landed B2 `07e121d9`, B3 `5c1b9ef8`, B4+B5
  `4ef3985f`, S2/S4/S7/S8/S9/S10 `ef247935`, LOG/REPORT `6f3aefad` (tip,
  tree clean) and correctly launched NO gate at the two-container cap
  (P1 + P6 mutation lanes live). It also overran the checkpoint clause
  (~104 calls) and disclosed it — accepted: the remaining work was
  bounded and a successor would have inherited a half-folded S set.
- Ruling: the registered gate `tester-unified` is a NON-mutation lane
  (RW-39: such lanes may run beside mutation runs under PSI). The
  controller launched it on `6f3aefad` at 21:08Z (pid `4180329`, log
  `<scratchpad>/p7-gate6.log`; the driver's own `docker run --rm` has no
  `--name`/`--cpus` — the controller caps it to 2 CPUs by `docker update`
  once it appears, so the three containers sum to 8). PSI `full avg10`
  0.10 at launch. HEAD of `assay-liveness` must not move until the log's
  `exit <n>` line exists.
- Deferrals S1 (ignore-guard + cleanup policy), S3 (`MutationStateError`
  reason mapping), S5 (hot-loop perf), S6 (`MUTATION_BUCKETS` help text)
  are accepted as follow-ups on condition they are rows in
  `assay/nyxloom-trove/4-backlog.md`; the reviewer checks, the controller
  adds any missing row after the gate has finished.
- Reviewer `a6018b6be13b42937` resumed for round 2 (repair diff
  `8f972def..6f3aefad`, per-blocker checklist, RW-49 D1–D3 named as
  settled, gate log to be read in a separate step, no second gate
  container). Round 3 is the last under the cap.

### RW-54 — 2026-09-12 21:13Z — P2's final pass failed at the R0 baseline;
diagnose by hand first, relaunch on a stated condition; P4's r2 moves
behind P2's release

- P2's short `--resume` pass (pid `4137438`) ended `FAIL/COMMAND_FAILED`
  (exit 1) at 21:05Z INSIDE the baseline pytest — no candidate ran, the
  281-record cache is intact, the tree is still detached at `186461de`
  and clean. assay 6.1.1 keeps no raw output on `COMMAND_FAILED`, so the
  failing tests are unknown. At the time: three `tester-unified:local`
  containers (P1 r2, P6 r2, the P7 gate), P4's bare r1 pytest, CPU PSI
  `some avg10` ≈ 45, memory `full avg10` 3.5. P2 correctly did not
  relaunch (RW-41's pre-authorised relaunch covered budget trips, not
  FAIL) and reported.
- Controller check: the `/tmp/run-gate-shared-*-<pid>.lock` and
  `run-gate-exec-*-<pid>-runner.lock` entries are pid-scoped, so the
  stale-lock hazard P4 fixed (RW-46a) cannot hit a fresh process;
  `/tmp/run-gate/` holds only lane logs (5661). Contention is the
  leading hypothesis, unproven.
- Ruling: P2 runs the baseline by hand (bare, serial, `-rfE`, no
  coverage) to get the names, classifies (a) timing/contention,
  (b) environmental, (c) genuine; relaunches the pass for (a) or a clean
  by-hand pass only once the P7 gate container is gone, memory PSI < 5
  and CPU PSI `some avg10` < 25; (b) needs the foreign state named to the
  controller first; (c) no relaunch. A second baseline failure under
  that condition stops the track for a controller decision on accepting
  the run-2 verdict (281/283 judged) with a disclosure.
- P4 (supersedes the RW-52 launch condition): no r2 until P2 has
  released 23.7.0; then merge `main` into `rg55-followups-run-gate`,
  re-run selftest/r1/r3 on the merge tip and judge THAT tree — the
  reviewed tree and the merged tree stay one tree, and P2's short pass
  is not contended by a 4 h sibling. P4 returns after B5 + the
  non-blocking items + selftest/r1/r3.

### RW-55 — 2026-09-12 21:17Z — a closed P4 session re-woke and acted as a
controller; stopped; P4 session 6's early r2 terminated

- P4 session 4 (`ade7916e85220fbb9`, closed ~19:30Z after round-1
  repairs) was re-invoked at ~21:25Z — most likely by one of its own
  tracked background watchers finishing when P2's run exited — and,
  with stale context, "processed" old notifications, messaged P4
  session 6 with options about its r2, and armed a watcher on P2's
  by-hand log. It wrote nothing. The controller stopped it (`TaskStop`),
  which also kills its watchers.
- P4 session 6 had launched its assay-r2 at 21:14Z under the RW-52
  condition before the RW-54 correction reached it. Ruling: terminate
  it (its own pids only; no container; ~15 min lost) — P2's short pass
  must re-run without a 4 h CPU sibling, and round 3 judges the
  post-merge tree (RW-54). Session 6 told to ignore the stale agent.
- Estate rule (memory `subagent-watchers-reinvoke-closed-sessions`): a
  superseded implementer session with tracked background commands is
  TaskStop'ed by the controller when its successor is dispatched, never
  left "completed" — its watchers re-invoke it with stale context.

### RW-56 — 2026-09-12 21:26Z — P2's baseline failure root-caused: an
order-dependent lock-directory plant; final pass runs with
`PYTEST_ADDOPTS="-p no:randomly"`, tree unchanged

- P2's by-hand baseline (3 failed / 1084 passed, 194 s): all three in
  `TestExecModeMutex` — `IsADirectoryError` on
  `/tmp/run-gate-exec-myproj-dev1-<pid>-runner.lock` at
  tests/test_run_gate.py:3949 and :3968, and "DID NOT RAISE" at the
  lock-released test because `main()` took the unusable-lock infra path
  first. The directory is planted by
  `test_unusable_lock_path_is_infra_failure_not_traceback` (line 4036,
  `mkdir()` at 4053) with no cleanup; the name is pid-scoped, so it
  poisons every later `os.open` of that path in the SAME process.
  pytest-randomly is active (RG-62), so the plant precedes its siblings
  on a per-run coin flip: runs 1–2 passed, run 3 and the by-hand run
  did not. Not contention; not the cross-process hazard (pid-scoped).
- Why not fix the tree: a commit invalidates the 281 judged records
  (RW-41) for a defect P4's branch already fixes (RW-46a isolation +
  cleanup). Ruling: the final 2-candidate pass runs with
  `PYTEST_ADDOPTS="-p no:randomly"` (definition order puts the plant
  after its siblings), proven deterministic by three by-hand runs first,
  disclosed in P2's REPORT; test order changes nothing about which
  mutants the suite kills. If assay strips the environment the baseline
  fails identically and the track stops for a decision. Relaunch gate:
  memory PSI only (RW-52).
- P4 session 6 parked at `1f8d9ca3` (B5 + S6–S11, selftest/r1/r3 green,
  r2 terminated per RW-54/55), waiting for the 23.7.0 release.

### RW-57 — 2026-09-12 21:42Z — P7 round 2 REJECT on B6 (false `hung`
under xdist); rulings for the repair; session wind-down begins

- Gate `tester-unified` on `6f3aefad`: `exit 0` (history `latest` =
  `6f3aefad`, `dirty: false`, 1691.6 s, 21:08Z). Reviewer round 2
  (`run-gate-WAVE-RG55-P7-REVIEW-round2.md`, committed): B1–B5 all
  verified end-to-end with the round-1 probes re-run verbatim; `liveness.py`
  100 %/100 % reproduced; S2/S4/S7–S10 folded; survivor table N/A accepted
  (assay's own lane is R0-only, A-046/A-133).
- B6 (new, present since `8f972def` — a round-1 miss, not a regression):
  with an events file written by several pytest processes (xdist `-n 2`
  under assay's own injection shape → 3 `session_start`/`session_finish`),
  the `session_finish` disjunct at `liveness.py:1214-1217` arms the fixed
  30 s `_HUNG_SESSION_FINISH_GRACE_S` at the FIRST `session_finish` and
  consults neither CPU nor later events → `LivenessHungExpired` at t≈35 s
  on a progressing candidate → the lane ends `BUDGET_EXCEEDED/
  CANDIDATE_HUNG`. Reachable today via `vbpub/nyxloom/assay.toml`
  `[lanes.session-extract]` (`-n auto`, `budget_per_candidate = "120s"`,
  liveness `auto`). Same cause double-counts `tests_completed` and shrinks
  `worst_gap_s`. Never a false survivor (`os._exit` under xdist measured
  correct).
- Rulings (decision asks 1–3): (1) B6 is MERGE-BLOCKING. Repair = the
  reviewer's minimum conjunct (the post-`session_finish` grace may expire
  only while the candidate's process tree is CPU-idle for the whole grace,
  i.e. `and idle_for >= _HUNG_SESSION_FINISH_GRACE_S`) + a regression test
  with the xdist shape (multi-process events file, growing CPU, an event
  every 2 s, calibrated bound 600 s → must NOT expire); pid-stamping the
  events (`_append`) + per-pid parsing (correct `tests_completed`/
  `worst_gap_s` under xdist) is a filed follow-up row, not this release.
  (2) RW-49/D3 "trailing gap" = last event → `session_finish` as
  implemented, accepted BECAUSE the post-`session_finish` region is then
  guarded by the idle-conjunct grace (N2's residual measured ~1 s with
  `--cov`; documented, not a defect). (3) N1: the four deferral rows
  (S1/S3/S5/S6) plus the pid-stamping row and N3 (`crashed` in
  `mutation_pct`'s enumeration) are filed in
  `assay/nyxloom-trove/4-backlog.md` in the repair commit; N4/N5/N6 are
  recorded in the REPORT.
- No repair dispatched: the operator instructed the session to wind down
  (no new work; agents checkpoint to files). Round 3 (the last under the
  cap) is the next session's: a FRESH Opus repair successor seeded with
  the round-2 file + P7's LOG/REPORT session-10 sections, one gate run on
  a quiet tip, then the reviewer round 3 (a fresh reviewer seeded with
  rounds 1–2, since this session's reviewer cannot be resumed from a new
  session).

### RW-58 — 2026-09-12 21:45Z — P2: the RW-56 remedy works by hand but not
through assay; relaunch rule for the successor

- P2's second by-hand baseline with `PYTEST_ADDOPTS="-p no:randomly"`
  (bare, niced, `-rfE`): 1087 passed, 3 skipped, 153.9 s — GREEN. The
  relaunched assay pass with the same variable exported (21:22Z) still
  ended `FAIL/COMMAND_FAILED` in the baseline at 21:24Z. Conclusion for
  the successor (to confirm by reading assay 6.1.1's runner): assay does
  not propagate `PYTEST_ADDOPTS` (or the environment) into the baseline
  command, so the order-dependent defect (RW-56) stays a per-run coin
  flip inside assay.
- Rule: the successor relaunches the 2-candidate `--resume` pass on the
  tree detached at `186461de` up to THREE more times (≈6 min each; only
  the R0 baseline is at risk; records intact); the first PASS is the
  evidence. If all three fail in the baseline, accept run 2 (281/283
  judged, 263 killed, 18 survivors triaged, the 2 placeholders named as
  never executed, RG-62/RW-56 cause) as the mutation evidence with that
  disclosure in the REPORT and CHANGES, and proceed to release 23.7.0.
  Either way P4's branch (RW-46a) removes the defect for 23.8.0.

### RW-381 — 2026-09-29 05:11:05Z — provisional merge after short gates and review

For RG-55 packages whose only remaining evidence is a long mutation or full
gate, the controller may merge provisionally with `--no-ff` after exact-tip
100% line and branch coverage, all required short lanes, required live safety
probes, and a fresh independent Sol xhigh review are green/ACCEPT. This
unblocks dependent package work; it does not authorize release, installation,
or a DONE claim. Run the outstanding long mutation and full gate in a separate
CIU worktree at the merged candidate, keep that judged tree quiet, and retain
Assay's per-tree resume identity. If either campaign finds a repair, implement
and backport it outside the judged tree, then judge the repaired candidate as a
new exact tree. Host load or contention must not change lane verdicts or be
used to excuse an otherwise failing candidate.

## Dispatch

| package | worktree | branch | implementer | reviewer | status |
|---|---|---|---|---|---|
| P1 — cgroup-profiler daemon | `.worktrees/rg55-profiler-daemon` | `rg55-profiler-daemon` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | sessions 1–5 → C0–C9 + live acceptance (`8cdd09e6`, RW-19 `71c6f607`); orphaned r2 container exited 15:49Z (208 candidates, 195 killed, 13 survived on the OLD tree `7ec4f9e8`); `budget_per_candidate` key misplaced in `16f3a29f` → fixed `5ce232d1`; r2 re-judge under run-gate ownership 15:58Z–19:33Z re-ran all 208 (per-tree identity, RW-41) and hit the lane budget at ~201/208 → relaunch detached at `5ce232d1` (container destroyed by the controller 19:47Z, RW-47; relaunched 19:48Z, 207 records resumed) → still `BUDGET_EXCEEDED` 20:00Z = the hung mutant (RW-48) → RW-48 root fix + second survivor pass (8 killed, 4 justified) `637b8c09`, proven by hand (mutant → 1 failure, no hang, 122 s); FULL fresh r2 launched 20:20Z (container `run-gate-vbpub-r2-3677631-…`, ~23:30Z); then r0-r1/r3 → reviewer |
| P6 — cgroup-profiler follow-ups (CP-2 socket, CP-4..CP-7, cgprofile.slice, CP-8 watch, CP-9 placement) | `.worktrees/rg55-followups-cgprofile-final` | `rg55-followups-cgprofile-final` | controller-owned continuation | fresh GPT-6-Sol xhigh (never a fork) | Round-5 fixes `da066287` reconciled with main `038644c0` at `9a472dec`. Exact tree `f4872603` passes r0-r1 (1,799 tests, 100% line/branch coverage) and r3 (7/7 canaries rejected); the P6 review handoff is now being reconciled to RW-379..381, so rerun both on the resulting clean tip. Required live safety probes and fresh Sol round 6 remain. Under RW-381, provisional `--no-ff` merge follows final green short lanes, live probes, and Sol ACCEPT; run replacement R2 and full gate in a separate quiet CIU worktree at the merged candidate. No release until both pass. |
| P7 — assay B091 (progress-judged candidates) | `.worktrees/assay-liveness` | `assay-liveness` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | dispatched 2026-09-12 ~13:55Z from `main` (RW-29); session 1 → A1 (`de32bb91`), BRIEF-1 `723c431d`; session 2 → spike + plugin + v1 runner (`f4fa1788`), BRIEF-2 `ef5088f6`; session 3 → RW-36 gating `e27b107b` + verify.py fix, BRIEF-3 `d2b7c76d` (A3 mechanism decided: `LivenessHungExpired` + `ReasonCode.CANDIDATE_HUNG`); session 4 → A3 active runner + `hung` bucket `44dd12ca` (a real `judge_mutation` precedence bug fixed; 252 calls — clause violated, flagged), BRIEF-4 `4ace234f`; session 5 → A3 complete: e2e CLI tests `99463ae5`, boundary tests + mutant table `d1540eda` (real bug: plugin wrote `repr` not JSON — fixed), BRIEF-5 `72baf838`; session 6 → A4 `5baf2670`, A5 `c15f6040` (275 calls — clause violated again), BRIEF-6 `1eaf5683`; session 7 → A6 docs/backlog/B092 `afda58fd`, sweep 2077 green `8bf77745`, W7 schema re-sync `b3f31506`, tip `edb995f4`; registered gate (`tester-unified`, wheel-in-container) RED 18:30Z: 5 failed / 4681 passed (pyflakes, 3 real-R2-through-the-wheel standalone tests, RecursionError sweep) → session 8: 5 root causes fixed `95d02f50`/`ee24ced6` (dead imports, RecursionError on the side-file parser, stale wheel-R2 expected documents), tip `8f972def`; gate re-run 2 voided by a records commit moving HEAD mid-run (assay `NO_MEASUREMENT/HEAD_CHANGED`, 4686 passed); controller relaunched from the clean tip → GREEN `exit 0` 19:44Z; review round 1 REJECT B1–B5 (~20:20Z, RW-49) → session 9: B1 `802f0855`, BRIEF-9 `2c967cae`; session 10 (Opus — B2 calibration carries design judgment) dispatched ~20:35Z for B2–B5 + gate → B2 `07e121d9`, B3 `5c1b9ef8`, B4+B5 `4ef3985f`, S-items `ef247935`, tip `6f3aefad` (10 planted mutants caught, `liveness.py` 100%/100%; S1/S3/S5/S6 deferred; ~104 calls, disclosed); gate NOT run at the container cap → controller launched `tester-unified` on `6f3aefad` 21:08Z (RW-53) → GREEN `exit 0` (1691.6 s); reviewer round 2 (21:10Z–21:42Z) REJECT on B6 only — false `hung` under xdist multi-process events files (B1–B5 verified) → RW-57 rulings; repair + round 3 deferred to the next session (wind-down) |
| P8 — mdt host-setup `dev-gates.slice` (dev-infra withdrawn, RW-30) | `.worktrees/mdt-dev-slices` | `mdt-dev-slices` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | dispatched 2026-09-12 ~13:55Z from `main` (RW-29); tip `7bd2f03c` review round 1 ACCEPT-conditional B1–B6 (~14:50Z) → repair set + M5 landed `ae38d55a` (~15:30Z, gate green); round 2 ACCEPT-conditional (B7/B8 new, B1–B6 + M5 PASS, RW-37) → repairs `e326cc9b` (gate green, ~16:00Z); round 3 ACCEPT (~16:10Z) → MERGED `a71c46b0` (RW-38); operator installs on the host BEFORE any devcontainer rebuild |
| P4 — run-gate follow-ups (RG-57..61) | `.worktrees/rg55-followups-run-gate` | `rg55-followups-run-gate` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | dispatched 2026-09-12 ~13:00Z from `186461de` (RW-27); C1–C4 committed (`a6716422`, `b5e4a9c6`, `e698835f`, `c37b6e94`); session 2 → merged `rg55-run-gate-client`@`647a2cc6` (`0c782601`), C5 `5b80c024` (RG-61 sweep except item 5, budget 900s, rev 42), BRIEF-2 `b695db00` (304 calls — clause violated); session 3 → selftest PASS (1008/1008 lines, 376/376 branches; 13 tests added `e0e02dce`), r1 PASS, r3 PASS (canary re-anchored `7539a44e`), `run-gate.footprint.json` + CONSUMERS transcript `02707e30`, BRIEF-3 `0bb3bbeb`; r2 pending a slot (RW-42); review round 1 ACCEPT-conditional B1–B4 (~17:40Z, RW-43) → session 4 repairs `a1cebacf`/`8c5af489`/`05193f44`/`9489bb6d`/`50684f2c`, tip `00a79de4` (507 calls — clause ignored), selftest/r1 RED on the shared-lock hazard → session 5 (RW-46): lock-dir isolation + root cause, floor_bytes, S1–S5, footprint regenerated, selftest PASS 1143/100%, r1 PASS, r3 PASS, tip `4fa46b03` (357 calls — clause ignored; RG-62 used for two pre-existing flaky tests → P5's row becomes RG-63); review round 2 ACCEPT-conditional (B5 only, ~20:45Z, RW-51) → session 6 dispatched for B5 + non-blocking + transcript → tip `1f8d9ca3` (B5 + S6–S11, selftest/r1/r3 GREEN); its 21:14Z r2 terminated (RW-54/55); parked until 23.7.0 is released → merge `main`, selftest/r1/r3, r2 on the merge tip; round 3 = r2 survivor table + B5 |
| P2 — run-gate client | `.worktrees/rg55-run-gate-client` | `rg55-run-gate-client` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | sessions 1–7 → C1–C8 complete (`62d9a66a`, rev 41, footprint manifest from a live probe); review round 2 ACCEPT on `186461de` (close-out commits); assay-r2 hit the 4 h lane budget at ~165/283 (16:28Z) → RESUMED untracked (RW-40); the LOG-commit relaunch rejected all records (per-tree identity, RW-41) → re-run detached at `186461de` (pid `1499375`, 156 resumed; candidate 105 SIGKILLed by the controller, RW-50) finished 21:01Z with 2 placeholders unjudged → final short `--resume` pass pid `4137438` launched 21:02Z (RW-52); then `git switch rg55-run-gate-client`, survivor triage (state candidate 105's classification), final gates, merge --no-ff, release 23.7.0, install |

### RW-59 — 2026-09-13 02:30:59Z — controller takeover

The new controller has taken over RG-55 from the 2026-09-12 checkpoint. The
handoff, this log, the plan/contracts, and the named per-package briefs are
the controlling record. Resume the prescribed order: observe the detached P1
and P6 mutation runs, finish P2, repair/review/release P7, then P4, P1, P6,
P5, and P3 close-out. Do not reopen settled D-1..D-30 or the recorded RW-1..
RW-58 rulings; record any new product call as a new D-decision and operational
calls as RW rulings. The operator's exclusions and dirty-file protections in
the handoff remain binding.

### RW-60 — 2026-09-13 02:36:07Z — triage of the shared RG-45 backlog edit

The shared checkout's uncommitted addendum under RG-45 is retained as
operator-authored evidence. It describes a distinct lane-budget symptom of
the already filed/moved assay B078/vitest heartbeat issue, and does not belong
in P2's 23.7.0 or P4's 23.8.0 code trees: the P2 auto candidate budget and P7
liveness work do not silently solve a fixed whole-lane budget. No new RG id or
code change is invented here. The addendum is not present in the P2/P4 branch
copies; preserve it for the operator's eventual backlog commit and carry its
disposition into P3's close-out report. Do not stage or commit it as part of
the controller's shared-checkout LOG work unless the operator explicitly
claims that file.

### RW-61 — 2026-09-13 10:27:26Z — abort P2 R1 launch after PSI crossed the gate

The second P2 final-gate R1 attempt was launched after a low preflight, but
the run-gate client immediately measured `memory full avg10=11.71%`, above the
estate launch ceiling. It had only just started; the controller interrupted
the bare-host assay process, confirmed its child pytest was gone, and will not
launch another gate until a fresh PSI reading is below 5%. This run is not
evidence: the earlier R1 PASS was on the pre-canary tip, and this interrupted
attempt is discarded. No product conclusion or ruling is changed.

### RW-62 — 2026-09-13 10:48:23Z — P7 repair accepted and merged

Sol xhigh's fresh final review round 3 ACCEPTed P7 on `cd1f84fe` with no
blocker. The registered tester-unified gate on that exact tip exited 0, with
wheel installation, B006(a) R0/R1/R2/R3 PASS, independent self-hosting PASS,
and pyflakes clean. The controller closed the stale session-10 gate notes,
committed the gate record, and merged branch `assay-liveness` with `--no-ff`;
P7's release remains pending cmru's local-snapshot requirement and a PSI-safe
launch.

### RW-63 — 2026-09-13 10:50:05Z — cmru release requires committed main at origin

`cmru release --project assay --set-version 6.2.0` and the same command with
`--ref HEAD` both refused before mutation because committed local `main` was
ahead of `origin/main` (41, then 42 commits after the P7 merge). `--ref` selects
the comparison ref but is not an override for the pushed-snapshot safety gate.
The controller will push committed `main` as the normal release workflow;
the operator's dirty `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` remains
unstaged and is excluded from that push.

### RW-64 — 2026-09-13 10:51:14Z — preserve concurrent origin updates by merge

The first push was rejected because `origin/main` had advanced by nine
committed release-preparation commits. The controller fetched and merged
`origin/main` with `--no-ff` into local `main`, preserving both histories and
the operator's dirty backlog outside the index. Local `main` is now one
connected committed history and is ready for the normal push; no reset, rebase,
force-push, or dirty-file commit is permitted.

### RW-65 — 2026-09-13 10:53:52Z — P2 final review accepted

Fresh Sol xhigh adversarial review round 3 accepted P2 with no blockers. The
review verified the repaired R3 canary (`2 rejected, 0 survived`), retained
R0/R1 evidence, exact 875/875 changed-line and 336/336 changed-branch
coverage, doctor, R-36h containment, and the RW-58 mutation disclosure.
The review record is `run-gate-WAVE-RG55-P2-REVIEW-round3.md`; merge remains
serial and release/install still follow.

### RW-66 — 2026-09-13 10:54:50Z — preserve operator backlog during P2 merge

The P2 no-ff merge was initially refused because the operator's uncommitted
`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` addendum would be overwritten.
The controller will use a path-scoped temporary stash solely to preserve and
restore that addendum around the merge; it remains outside all controller
commits and is not a product decision.

### RW-67 — 2026-09-13 11:20:26Z — assay 6.2.0 released

The authorized cmru release transaction completed successfully. Its
registered tester-unified gate passed in 1322.2 seconds, the release was
tagged and published as `assay-v6.2.0`, and the hash-bound artifacts are
retained under `assay/artifacts/assay-v6.2.0/`. cmru could not synchronize the
local checkout automatically because the operator backlog is dirty; the
controller will merge the already-pushed release commit with `--no-ff` while
preserving that dirty file outside the index.

### RW-68 — 2026-09-13 11:23:23Z — verify assay deployment and changelog

The published assay wheel's sidecar hash matches the retained local artifact,
and `/home/vscode/.venv/bin/assay --version` reports `6.2.0`. The stale
post-release `[Unreleased]` body was cleared in a docs-only commit and pushed
to `origin/main`; the operator backlog remains the only working-tree change.

### RW-69 — 2026-09-13 11:24:01Z — run-gate release excludes operator backlog

The first `cmru release --project run-gate-project --set-version 23.7.0`
attempt refused before creating a transaction because the shared checkout has
the operator's uncommitted backlog addendum. The controller will rerun with
cmru's explicit `--allow-uncommitted` escape hatch: the release source is the
pushed `origin/main` snapshot and the known dirty path is excluded by the
operator-file rule. No gate or release mutation ran in the refused attempt.

### RW-70 — 2026-09-13 11:27:50Z — run-gate 23.7.0 released

The authorized cmru transaction released `run-gate-v23.7.0` after its
selftest gate passed in 157.1 seconds, published the wheel and tag, and
retained the artifact manifest under
`run-gate-project/artifacts/run-gate-v23.7.0/`. As with assay, cmru's local
sync warning is caused only by the preserved operator backlog; the controller
will merge the pushed release commit with `--no-ff`, install the exact wheel,
and verify revision 41.

### RW-71 — 2026-09-13 11:48:28Z — P4 merge-tip mutation gate is not evidence

P4's final R2 invocation on merge tip `d4c57c1a` returned
`INCONCLUSIVE/NO_MUTANTS`, not a green mutation result. Assay's B008
first-parent merge resolution selected `b72cba31` as the comparison base,
therefore the merge itself exposed no changed executable lines even though the
P4 source diff exists. The controller rules this run invalid for the P4
mutation claim and requires a resumed R2 on a non-merge P4 judged tree, with
source identity checked against the final merge tip; the already-green R1/R3
results remain separate final-tip evidence. This is a gate-procedure ruling,
not a reopening of any settled product decision.

### RW-72 — 2026-09-13 12:00:28Z — exact-tree non-merge assay judgment

The P4 source comparison verified that the complete final source tree at
`d4c57c1a` is represented by ephemeral non-merge commit `cd6780ed` (parent
`fccba080`; identical tree), while the existing non-merge tip before the P2
canary merge was missing four source lines. P4 R2 is therefore judged on
`cd6780ed`, with its exact tree identity and `--request-base main` recorded in
the report; the ephemeral commit is not a release or product-history commit.
The final branch remains at `d4c57c1a` until the valid mutation evidence and
final review are complete.

### RW-73 — 2026-09-13 15:17:21Z — mutation campaigns completed; triage/resume required

The resumed P4 mutation campaign on the exact non-merge synthetic tree
completed 57 candidates with 43 killed and 14 survived. The fresh P1 campaign
completed 208 candidates with 203 killed and 5 survived. The resumed P6
campaign reached its lane budget after 484 candidates (368 killed, 40
survived, 76 `budget_exceeded`), so it remains a BUDGET_EXCEEDED lane and must
be resumed from the exact judged tree until every candidate is judged. The
detached Luna implementer sessions expired at the platform usage limit after
the campaigns; the controller may seed fresh Luna xhigh sessions now that the
window is available. No package is merge-ready until survivor triage, final
gates, and the fresh Sol xhigh review are complete.

### RW-74 — 2026-09-13 15:31:32Z — P1 closeout evidence is ready for review

Fresh Luna xhigh closeout completed P1 on branch `rg55-profiler-daemon` at
`920231186fdfb81ea3d9e9adb2d0b57ed11ab6fd`. The five fresh-r2 survivors are
all explicitly dispositioned (four behaviorally equivalent, and the prior
summary oracle gap is killed by the focused test). Final `r0-r1` and `r3`
both exited 0 with 100% line and branch coverage and 7/7 canaries rejected;
D-15 safety was checked and the daemon is down. A fresh Sol xhigh review is
required before this branch can merge.

### RW-75 — 2026-09-13 15:34:08Z — Sol xhigh review dispatch not honored

Two fresh review dispatches requested with model `gpt-5.6-sol` and
`xhigh` reasoning identified themselves as GPT-5/Codex and refused to certify
the review. Neither changed files or created a commit. The controller counts
neither as a review round and will not merge P1 without a reviewer whose
runtime identity is actually Sol xhigh; implementation and mutation work may
continue meanwhile.

### RW-76 — 2026-09-13 15:36:45Z — P4 survivor-oracle repair requires fresh judgment

Fresh Luna xhigh triage committed P4's focused survivor tests and updated
LOG/REPORT at `12e4e150`. The branch is clean and the previously surviving
P4 mutants are now covered by explicit oracles; because the judged test tree
changed, the controller requires a fresh R2 on this committed tree before
review or merge.

### RW-77 — 2026-09-13 16:58:01Z — continue P6 from ten remaining placeholders

The first P6 resume after the handoff ended at exit 4 with cumulative counts
484 candidates, 431 killed, 43 survived, and 10 `budget_exceeded`. It made
progress through the 66-candidate remainder but did not judge the last ten;
the exact container exited normally without OOM. The controller requires
another PSI-gated `--resume` on the same judged tree until
`budget_exceeded=0`. In parallel, P4's fresh R2 remains active after its
triage commit.

### RW-78 — 2026-09-13 17:05:40Z — P4 fresh judgment leaves one survivor

P4's fresh R2 on the repaired tree completed normally with 57 candidates,
56 killed, one survived (`run-gate.py:2065`, `LtE->Lt`), and zero
budget/crashed candidates. The controller requires Luna xhigh disposition of
that single survivor and, if it is an oracle gap, another exact-tree R2 before
P4 review or merge.

### RW-79 — 2026-09-13 17:14:39Z — Sol xhigh reviewer runtime unavailable again

A fresh final-review dispatch for P1 was explicitly constrained to
`gpt-5.6-sol` at xhigh. The returned runtime identified itself as GPT-5/Codex,
refused the assignment, ran no probes, wrote no review, and created no commit.
This is not a review round and does not satisfy the pre-merge reviewer gate;
P1 remains merge-blocked until a genuine Sol xhigh reviewer completes the
adversarial review.

### RW-84 — 2026-09-13 18:39:47Z — Sol xhigh P4 reviewer runtime unavailable

A fresh final-review dispatch for P4 was explicitly constrained to
`gpt-5.6-sol` at xhigh. The returned runtime identified itself as Codex based
on GPT-5, stopped immediately, ran no probes, wrote no review record, and
issued no verdict. This is not a review round; P4 remains merge-blocked until
a genuine Sol xhigh reviewer completes the adversarial review.

### RW-80 — 2026-09-13 17:40:27Z — P6 resume advances but remains budget-incomplete

The exact P6 resume container
`run-gate-vbpub-r2-2892668-1789318878` exited 4 with `oom=false`. Its
separately read cumulative verdict is 484 candidates: 436 killed, 43
survived, 5 `budget_exceeded`, and 0 crashed. The lane remains unfinished;
the controller requires another PSI-gated exact-tree resume until no budget
placeholders remain.

### RW-81 — 2026-09-13 17:43:26Z — P6 exact-tree resume relaunched

After the RW-80 pass, the controller woke the Luna xhigh P6 implementer and
confirmed a new exact-tree resume. Container
`run-gate-vbpub-r2-3305136-1789321303` started at 17:41:46Z with
`oom=false`, `NanoCpus=3000000000`, and `dev-background.slice`; its baseline
was observed running. No detached-tree switch or commit is permitted until
this resume exits.

### RW-82 — 2026-09-13 18:16:44Z — P6 timeout mutants are deterministic

The RW-81 P6 resume exited 4 with `oom=false` and left the same five
`budget_exceeded` candidates: `lib/serve.py:610` `Or->And`, `:1461`
`Is->IsNot`, `:1700` `Eq->NotEq`, `:1700` `And->Or`, and `:2034`
`True->False`. The cumulative evidence remains 436 killed, 43 survived, 5
budget, 0 crashed out of 484. Blind retries are paused; Luna xhigh must
triage the causal hang and make a focused repair, if warranted, before a
fresh judged-tree R2.

### RW-83 — 2026-09-13 18:26:56Z — P4 fresh R2 is green

P4's fresh exact-tree R2 completed normally on the repaired tree. The
separately read progress/verdict records show 57 candidates, 57 killed, 0
survived, 0 `budget_exceeded`, and 0 crashed; the assay process exited 0.
P4 may proceed from the detached judged tree to its branch for final gates,
but no detached-tree commit was made.

### RW-85 — 2026-09-13 18:41:50Z — P6 fresh Luna successor for gate mechanics

The prior P6 Luna session completed the causal triage but did not advance the
remaining budget placeholders. It was TaskStopped before a successor was
dispatched. A fresh Luna xhigh implementer was dispatched from committed
BRIEF-10/LOG state to test the minimal mutation-argv fail-fast configuration
(`pytest ... -x`) or reject it with evidence, then commit any justified
non-production fix and rerun the exact judged tree. No reviewer, merge, or
release was dispatched.

### RW-86 — 2026-09-13 18:46:24Z — terminate stale orphaned pytest

The controller found an unrelated orphaned non-mutation pytest process
(`PID 2482699`, scratchpad cwd, age over two days, 0% CPU, waiting for a
missing partner). It was not part of any live RG-55 lane. The controller
terminated that exact process with SIGTERM and verified it was gone; no
files or worktrees were changed.

### RW-87 — 2026-09-13 18:52:05Z — checkpoint P6 successor and narrow the task

The first fresh P6 Luna successor did not produce a shell/test result or
commit after repeated wake/checkpoint messages, so it was TaskStopped before
any mutation or source change. A second fresh Luna xhigh successor was
dispatched from BRIEF-10 with a narrowed, bounded task: test whether adding
pytest fail-fast (`-x`) to the assay mutation argv is the minimal honest fix
for the five deterministic timeout mutants, commit only an evidence-backed
config/record change, and wait for controller approval before any new R2.

### RW-88 — 2026-09-13 18:58:43Z — approve fresh P6 R2 after fail-fast fix

Luna xhigh committed P6's evidence-backed one-token assay configuration fix
as `5c2134ed`: r2 now invokes `pytest tests -q -x`, matching the established
full-gate command. Assay config tests (72 passed) and the focused project
suite (103 passed, 6 skipped) are green; no production code changed. The
controller approves exactly one fresh R2 on this new tree, with assay resume
identity kept to the new judged tree and no commit while detached.

### RW-89 — 2026-09-13 19:04:36Z — launch the approved P6 R2

The approved fresh P6 R2 was launched from detached tree `5c2134ed` after the
memory-PSI gate (`full avg10=1.35`). The exact container is
`run-gate-vbpub-r2-3944182-1789326240`, running under `dev-background.slice`
with `NanoCpus=3000000000`; no duplicate mutation lane was launched. The
controller will read the wrapper exit marker and assay verdict separately.

### RW-90 — 2026-09-13 19:09:47Z — Sol reviewer runtime unavailable again

A fresh P1 final-review dispatch requested explicitly as Sol xhigh reported
that its runtime identity could not be verified as exactly `gpt-5.6-sol` at
`xhigh`; it stopped before repository inspection, probes, or modifications.
This is not review evidence and P1 remains merge-blocked.

### RW-91 — 2026-09-13 19:15:05Z — Sol reviewer runtime unavailable for P4

A fresh P4 final-review dispatch requested explicitly as Sol xhigh exposed only
a generic GPT-5 identity and could not verify `gpt-5.6-sol` at `xhigh`; it
stopped before reading the handoff, inspecting the repository, probing, or
writing a review. This is not review evidence and P4 remains merge-blocked.

### RW-92 — 2026-09-13 22:14:41Z — fold assay B092 and B098 into the wave

The operator authorized immediate implementation of assay backlog B092
(explicit per-tree mutation identity exclusions) and B098 (complete the
`mutation_pct` excluded-bucket documentation). The work is isolated to a new
assay worktree and must not modify the active P6 judged tree or any other
package. The implementation and its tests use Luna xhigh; Sol is reserved for
an unsolvable Luna blocker. B092's public configuration, hash-identity
compatibility, validation, docs, and regression oracles are part of the work,
and B098 must retain the existing arithmetic while documenting `crashed`.

### RW-93 — 2026-09-13 23:00:05Z — discard non-verdict B092+B098 review session

The first fresh Luna xhigh adversarial reviewer was stopped after repeated
completion prompts produced no report or verdict. It modified no product
files; its partial evidence was 150 focused tests, 42 documentation checks,
67 additional targeted tests, and passing live loader/digest probes, with the
full serial suite interrupted at approximately 54%. Because no review report
or verdict was issued, this does not consume a review round or provide merge
evidence. A fresh Luna xhigh reviewer must be seeded from the same handoff;
Sol remains reserved for an actually unsolvable Luna issue.

### RW-94 — 2026-09-13 23:10:08Z — relaunch P6 asynchronously after budget checkpoint

P6's first four-hour run ended with 260 killed, 34 survived, and 190
budget-exceeded candidates, leaving 296/484 records judged. The controller
confirmed the detached judged tree remained exactly `5c2134ed`, the prior
inflight record was cleared, and memory PSI `full avg10=0.00` before launch.
A fresh asynchronous resume was then started from
`scripts/cgroup-profiler` with `--resume --progress` supplied by run-gate;
the exact container is `run-gate-vbpub-r2-4167718-1789340985` under
`dev-background.slice` with `NanoCpus=3000000000`. Its wrapper is detached
with an explicit `RUN_GATE_EXIT` marker in `/tmp/rg55-p6-r2-resume-2.log`.
The assay verdict and wrapper exit will be read separately when it finishes.

### RW-95 — 2026-09-13 23:14:57Z — continue with independent assay B096

The operator authorized continued progress while the long P6 mutation gate
runs asynchronously. B096 is adopted as a small, independent assay change:
derive the `--rejudge-outcome` CLI help's canonical bucket list from
`MUTATION_BUCKETS` while retaining the CLI-only `error` alias for `crashed`.
It is isolated in a successor assay worktree based on the accepted B092+B098
tip; it must not modify the running B092 gate tree or the P6 judged tree. The
implementation, tests, docs, and fresh adversarial review use Luna xhigh;
Sol remains reserved for an actually unsolvable Luna issue.

### RW-96 — 2026-09-13 23:33:43Z — dispatch independent assay B097

While P6's mutation resume and the B092+B098 registered gate remain
asynchronous, the controller opened assay B097 (P7 B6-b) in isolated worktree
`assay-b097`, based on the B096 branch. The package covers pid and optional
xdist-worker stamping, per-process event parsing, owner-only session-finish
detection, and legacy-record compatibility; it must preserve B6-a's
progressing-tail guard. The implementer is Luna xhigh, with focused tests,
the three adopter-facing documents, backlog, CHANGES, and a report required.
No mutation campaign, merge, release, or dstdns action is authorized by this
ruling; a fresh Luna xhigh adversarial review is required before any merge.

### RW-97 — 2026-09-13 23:34:58Z — triage operator RG-45 addendum

The shared checkout contains an uncommitted operator-owned addendum under
RG-45 documenting a distinct fixed-lane-budget timeout observation (dstdns
P192, 714.67 seconds against a 600-second lane budget). A repository-wide
search found no committed or worktree duplicate. It is already filed upstream
in the run-gate backlog; this wave neither edits nor commits it, and no
product decision is inferred from it.

### RW-98 — 2026-09-13 23:38:34Z — cap an independently discovered mutation lane

During the B096 gate launch, an unrelated exact container appeared:
`run-gate-vbpub-session-extract-10141-1789342673`, running the old
`session-extract-follow` mutation command under `dev-background.slice` with
`NanoCpus=0`. It was not launched by this controller, but it is an active
mutation lane on the shared host; the controller verified its exact command
and applied `docker update --cpus=3` to that exact name. The estate now has
two active mutation lanes (this lane and P6); no third mutation launch is
authorized.

### RW-99 — 2026-09-13 23:39:09Z — B096 accepted; combined assay gate running

The fresh Luna xhigh B096 adversarial review returned ACCEPT with no ranked
findings. Its report is being committed on `assay-b096`; the controller then
started the required registered `tester-unified` gate from the quiet combined
tip asynchronously. Exact gate container: `sweet_blackburn`, under
`dev-background.slice`, `NanoCpus=3000000000`; it built wheel
`assay-6.2.1.dev36+g2d80012a`. The gate is not yet a verdict: its terminal
markers and exit status must be read separately before merge.

### RW-100 — 2026-09-13 23:41:38Z — defer new launches under memory PSI

The shared host's fresh `/proc/pressure/memory` reading reached
`full avg10=14.75%`, above the wave's launch gate of 5%. Existing capped
containers remain undisturbed, but no new test, gate, or mutation process may
be launched until a fresh reading is at or below the threshold. B097 editing
may continue; its implementer was explicitly told to defer PSI-gated tests.

### RW-101 — 2026-09-13 23:56:25Z — B097 validation green; final review dispatched

The B097 implementer committed `1daf6e62`; controller validation initially
found and repaired one test-only missing `json` import in `a8ac0d5c`. The
serial PSI-gated focused suite then passed 116 liveness tests, and the
cross-document suite passed 42 tests. The review handoff was frozen in
`b83b9941`, and a fresh Luna xhigh adversarial reviewer was dispatched against
that exact tip. The B097 registered gate remains pending until review accepts;
no merge or release is authorized by this ruling.

### RW-104 — 2026-09-14 00:10:31Z — B097 review fix-verification

The fresh B097 Luna xhigh review found no behavioral P0/P1/P2 defect but
rejected the tip for one extra EOF blank line in the committed brief. The
controller removed that single blank line, confirmed
`git diff --check ee41553d..HEAD` clean, and committed the review report plus
whitespace fix as `23b75167`. The same reviewer is resumed for fix-verification
against that exact tip; no new review round or product repair is authorized
unless it reports another finding.

### RW-102 — 2026-09-14 00:04:58Z — B096 registered gate failed qualification

The asynchronous B096 `tester-unified` gate passed its wheel-installed,
attestation, verdict-v5, schema-successor, verdict-v11, judge-provenance, and
self-hosted phases. Its existing Topos qualification then failed scenario
`current-full-pass`: expected exit 0, got 1. The exact wrapper recorded
`RUN_GATE_EXIT=1`; no B096 product source changed during the run. This is not
merge evidence. The gate must be rerun from the same quiet B096 tip after the
fresh reviewer verdict and a safe PSI reading, with the failure disclosed if
the required retry policy exhausts.

### RW-103 — 2026-09-14 00:06:57Z — disclose B096 retry launch PSI race

The controller intended to launch one B096 gate retry after a safe PSI check,
but the combined launch command's fresh reading was
`full avg10=11.41%`; the retry therefore started above the 5% gate. The exact
container `vigilant_ganguly` was capped immediately at `NanoCpus=3000000000`
under `dev-background.slice`. No additional launch will occur until a
separate fresh check is safe. This run's evidence remains provisional and its
launch deviation is disclosed in the final report; it cannot be treated as a
clean PSI-gated retry.

### RW-105 — 2026-09-14 00:11:57Z — B097 review accepted after fix-verification

The same fresh Luna xhigh reviewer accepted the B097 whitespace fix at
`23b75167`: `git diff --check ee41553d..23b75167` exits 0 and the fix delta
contains no product-file changes. The verification report was committed as
`aa075851`, making that the current quiet review tip. B097 still requires a
registered gate on its final tip before merge; the active B096 retry remains
the only tester gate and must finish first.

### RW-106 — 2026-09-14 00:21:47Z — dispatch P2 finalization in freed mutation slot

The independently discovered session-extract mutation container has finished,
leaving one of the two estate mutation slots available; P6 remains the other
active mutation lane. A fresh Luna xhigh P2 finalization implementer was
dispatched in `rg55-run-gate-client`, seeded with P2 BRIEF-7. It is authorized
to perform the prescribed up-to-three `assay-r2 --resume` attempts from
detached judged tree `186461de`, subject to a fresh PSI gate and immediate
3-CPU cap on its exact container, then survivor triage and final non-mutation
gates. It must not commit while detached and must not merge or release.

### RW-108 — 2026-09-14 00:29:31Z — clean B096 gate retry launched

The prior B096 retry completed `RUN_GATE_EXIT=0` and all terminal phases, but
was excluded as clean evidence because its launch PSI was above threshold. A
condition-guarded fresh launch then read `full avg10=2.79%` and started the
same unchanged B096 tip. Its exact container is `heuristic_jones`, under
`dev-background.slice`, immediately capped at `NanoCpus=3000000000`. This is
the clean-evidence candidate; its wrapper marker and terminal phases remain
pending.

### RW-109 — 2026-09-14 00:33:21Z — P2 finalization superseded by already-shipped state

The P2 finalization dispatch was stopped after the first prescribed retry
returned `NO_MEASUREMENT/BASE_IS_HEAD`: the handoff's judged tree
`186461de` is already an ancestor of current `main`, because the P2 client
was merged by `b54aa1f2` and its 23.7.0 release was recorded by the existing
wave history. Therefore `--base main` resolves to the detached HEAD and
cannot measure a delta. This is a stale handoff instruction, not a product
failure or valid mutation result; no further P2 retry, triage, merge, or
release is authorized. The Luna xhigh worker was interrupted without a
commit.

### RW-107 — 2026-09-14 00:27:40Z — P2 R2 retry attempt 1 running

The P2 implementer launched the first approved retry from detached judged tree
`186461de` with `PYTEST_ADDOPTS=-p no:randomly`, `nice -n 19`, and
`ionice -c 3`; its prelaunch run-gate reading recorded memory PSI
`full avg10=0.28%`. This is the bare-host lane, so no Docker mutation
container was created. The assay baseline is running under the same nice/IO
priority; the implementer will read its verdict separately from the wrapper
status and will switch back to the branch before any commit.

### RW-110 — 2026-09-14 00:34:17Z — dispatch fresh Luna final reviews for P1 and P4

The controller dispatched independent fresh Luna xhigh adversarial reviewers
for the still merge-blocked P1 daemon and P4 run-gate follow-ups. Each is
seeded with its package review handoff and exact current tip, may write only
its round-1 review file, and must perform the handoff's live probes and gates
under the host PSI rules. No Sol runtime is used; no product source or merge
is authorized by this dispatch.

### RW-111 — 2026-09-14 00:37:03Z — preserve historical P4 review rounds

The P4 worktree already contains the historical Opus review rounds 1 and 2,
including their committed conditional-acceptance records. The fresh Luna
xhigh reviewer dispatched under RW-110 is therefore assigned round 3 and may
write only `run-gate-WAVE-RG55-P4-REVIEW-round3.md`; it must not overwrite the
existing records. This correction changes no product or review evidence.

### RW-112 — 2026-09-14 02:43:52Z — controller snapshot and continuation plan

The controller has resumed the wave. The CMRU dirty-main release-cleanup
repair is complete at `f6972c98`, with the fresh Luna xhigh review accepted in
round 3 and the registered CMRU gate still running its mutation phase in exact
container `run-gate-vbpub-mutation-226339-1789351650`, capped at 3 CPUs.
The independent `session-extract-follow` assay lane is also active and capped
at 3 CPUs; it is not a mutation campaign.

Assay B092+B098 is accepted by its fresh Luna review at `176a06a5` but has no
registered final gate yet. B096 is accepted but its first gate failed Topos
qualification and its clean-PSI retry remains to be independently read;
B097's same-reviewer fix verification is accepted at `23b75167`/`aa075851`
but its final registered gate is still pending. P6 remains detached at judged
tree `5c2134ed`, with cumulative R2 outcome `BUDGET_EXCEEDED` and two
placeholders left to judge.

Continuation order is: finish/read the CMRU gate; resume P6's two placeholders
under the second mutation slot; complete the assay final combined gate on the
B097 tip and merge/release the resulting assay patch; repair P1 and P4 using
fresh Luna xhigh implementers and reviewers; finish their gates and releases,
then P6/P5 and the P3 live-probe/report close-out. No operator-owned dirty
file or `/workspaces/dstdns` content is in scope.

### RW-113 — 2026-09-14 02:46:16Z — relaunch P6 from the exact judged tree

The first resume invocation only re-attached to the already-collected
`run-gate-vbpub-r2-4167718-1789340985` container and returned its existing
`BUDGET_EXCEEDED/LANE_TIMEOUT` verdict; it started no new container and is not
new evidence. With a fresh memory-PSI reading of `full avg10=1.36%`, the
controller then used run-gate's explicit `--fresh` path from detached tree
`5c2134ed`. The old exact container had no inflight record to remove. The new
container is `run-gate-vbpub-r2-266908-1789353957`, under
`dev-background.slice`, immediately capped to `NanoCpus=3000000000`; no commit
has been made while detached.

### RW-114 — 2026-09-14 02:49:02Z — CMRU mutation survivors require repair

The registered CMRU release-cleanup gate reached its mutation result and
failed with three survivors among 43 candidates: `transaction.py:65`
`bool-const-flip` (`True->False` on the private result dataclass),
`transaction.py:1234` `bool-const-flip` (`check=False` on defensive rebase
abort), and `transaction.py:1253` `boolop-swap` in the abort-failure guard.
The result is not merge evidence. A fresh Luna xhigh implementer must
triage each survivor: add a real behavioral oracle or make an evidence-backed
equivalence/structure change, then rerun the required final review and the
registered gate on the new quiet tip. The worktree remains clean at
`f6972c98`; no release or install has been attempted.

### RW-115 — 2026-09-14 03:04:31Z — repair workers returned; P6 rejudge deferred

The CMRU mutation-survivor repair worker returned clean commit
`08692bf2`, adding meaningful frozen-result, failed-abort, and defensive
state-transition oracles. The P4 repair worker returned clean commit
`28bc3feb`, fixing synchronous exec-launch cleanup and correcting the current
os.wait4/own-child rusage documentation. Neither package has been reviewed or
re-gated after these commits.

P6's `--fresh` run from `5c2134ed` completed with the unchanged
`BUDGET_EXCEEDED/LANE_TIMEOUT` verdict: its baseline passed, but ordinary
`--resume` merged all 484 persisted records, including the two budget records,
without re-executing them. The explicit assay rejudge option is required for
those two records. A correctly mounted direct rejudge continuation is
prepared, but its fresh launch check read memory PSI `full avg10=11.96%`, so
the launch is deferred under the host gate. This exposes a run-gate follow-up:
run-gate's fixed `--resume` argv cannot request assay's supported
`--rejudge-outcome budget_exceeded` recovery path.

### RW-116 — 2026-09-14 03:08:11Z — launch the P6 explicit budget rejudge

The first manual direct-rejudge attempt used the correct dual-mount shape and
was immediately CPU-capped, but its command imported the P6 branch-local
assay CLI, which correctly refused the newer `--rejudge-outcome` option. The
exact failed container `run-gate-vbpub-r2-rejudge-20260914-030714` was removed
by name and its result is discarded; no P6 source or judged-tree commit was
changed.

After a fresh PSI reading of `full avg10=1.07%`, the controller launched the
explicit continuation using the released/main assay judge source, whose CLI
supports the option, against the unchanged P6 tree and resume store. Exact
container `run-gate-vbpub-r2-rejudge-20260914-030755` is running under
`dev-background.slice` with `NanoCpus=3000000000`. This is a continuation of
the registered P6 R2 evidence; the branch-local run-gate limitation and the
judge-source correction will be disclosed in the P6 report.

### RW-117 — 2026-09-14 03:14:49Z — P6 identity-preserving budget retry launched

The main-assay direct attempt was stopped before mutation because its newer
judge hash rejected all 484 old records. The controller therefore preserved
the original branch-local judge identity by moving only the two exact
`budget_exceeded` resume records into the recoverable ignored backup
`.assay/budget-retry-backup-rw116-20260914-031346/`. The remaining 482 records
were left untouched; the detached tree remains exactly `5c2134ed`.

After the PSI gate fell to `full avg10=4.19%`, the registered branch-local
`./run-gate.py r2 --fresh` was launched. Exact container
`run-gate-vbpub-r2-315586-1789355675` is running under
`dev-background.slice` and was immediately capped to 3 CPUs. This run should
select only the two pending candidates; its wrapper exit and assay verdict
will be read separately.

### RW-118 — 2026-09-14 03:24:44Z — controller takeover and PSI-aborted P4 gate

The current controller has taken over the checkpointed RG-55 wave under the
operator's Luna xhigh-only policy. P1's repair worker completed B1-B8 at
`72db8f8f`; its focused serial suite is `420 passed, 1 skipped`, with
compilation and diff checks green. P4's fresh repair verification is ACCEPT at
`28bc3feb` (review record committed as `74464fed`), but its required post-main-
merge registered gates are still outstanding.

A P4 `selftest` launch was attempted after a reading that raced with a host
memory-PSI rise; the observed `full avg10=10.55` exceeded the launch limit.
The controller stopped/confirmed absence of that just-launched process before
it produced any verdict. It is not gate evidence and will not be reported as
green. Future launches remain PSI-gated at `full avg10 <= 5`.

P6's valid identity-preserving retry has now completed with all 484 candidates
accounted for: 439 killed, 45 survived, 0 budget-exceeded, 0 crashed. The
verdict is therefore `FAIL/MUTANTS_SURVIVED`, not a budget exhaustion; the 45
survivors require explicit triage before P6 can be certified.

### RW-119 — 2026-09-14 03:27:06Z — CMRU evidence review rejects two survivor closures

Fresh Luna xhigh fix-verification of CMRU repair commit `08692bf2` returned
`REJECT` in `cmru-release-dirty-sync-REVIEW-round3-fixverify.md`. The
`check=False` survivor is properly closed, but the frozen private dataclass
test is implementation-detail evidence rather than a shipped behavioral
contract, and the `And->Or` test pairs a pre-rebase hook failure with a
monkeypatched active state that Git cannot produce in that transition. The
repair remains unmerged. A fresh Luna xhigh implementer is to replace those
two weak closures with either a contract-level immutable result construction
or removal of the unnecessary representation seam, and a real interrupted-
rebase oracle reaching active state after rebase state creation; then the
same mutation gate and a fresh final review are required again.

### RW-120 — 2026-09-14 03:42:29Z — CMRU evidence repair committed; final review pending

The fresh Luna xhigh CMRU repair worker replaced the private frozen dataclass
with an inherently immutable `NamedTuple` and replaced the manufactured
pre-rebase-hook/monkeypatch survivor test with a real post-rewrite interrupted
rebase fixture covering successful and failed aborts. Commit:
`e224579c096834f9d86906afbf2a73a4389775b7`. Verification reported
`1778 passed, 3 skipped`, compileall PASS, and diff-check PASS. The previous
round-3 fix-verification report remains committed as historical evidence; a
fresh independent Luna xhigh final reviewer is now reviewing this exact tip.

The operator-owned MDT release remains active at its cache-export step and is
outside RG-55's mutation/release worktrees. No gate is launched while memory
PSI exceeds the standing threshold.

### RW-121 — 2026-09-14 03:50:23Z — CMRU final review finds Git-config fixture dependence

Fresh Luna xhigh final review of CMRU tip `e224579c` returned `REJECT` for one
test-isolation blocker. The real interrupted-rebase fixture installs its hook
under `.git/hooks` without pinning the effective `core.hooksPath`, and its
postcondition checks only `rebase-merge`; valid `rebase.backend=apply` and
valid global `core.hooksPath` settings therefore defeat the oracle. The
NamedTuple replacement and default live probes passed. A fresh Luna xhigh
repair pass is dispatched to configure a repository-local hook path, accept
either Git rebase layout, and raise the child failsafe to 60 seconds; no CMRU
gate, merge, or release is authorized until a fresh final review ACCEPTs.

### RW-122 — 2026-09-14 04:01:36Z — resume controller operations and preserve the quiet-host gate

The controller resumes the RG-55 closeout under the operator's Luna xhigh-only
policy. The operator-owned modern-debian-tools-python-debug release remains
alive in its BuildKit cache-export phase and is not touched; memory PSI remains
above the launch threshold, so no new gate or mutation container is launched.
P6's 34-oracle-gap test repair is in progress on its branch with four test
files modified and no commit yet. P4's PSI-gated selftest watcher remains
parked. CMRU's fresh final review of repair `2eff6bdd` remains pending. The
next safe actions are to consume those results, launch the required quiet-tip
gates, and retain the previously stated P6 fresh-r2 critical path.

### RW-123 — 2026-09-14 04:04:32Z — cap P6 focused verification and keep it non-mutation

P6's implementer launched its focused regression suite in the exact container
`cgprofile-p6-focused-394169`. The controller verified that the command is
pytest-only (not an assay mutation lane) and immediately applied the standing
3-CPU cap; Docker reports `NanoCpus=3000000000`. This focused run is allowed to
continue while the host PSI gate remains closed for new mutation work.

### RW-124 — 2026-09-14 04:09:38Z — queue CMRU's registered gate behind host PSI

The fresh CMRU final review ACCEPTed exact repair tip `2eff6bdd`; its three
review artifacts are committed on `cmru-release-dirty-sync` as `443e4d75`.
The registered `./run-gate.py gate` was therefore queued from the clean CMRU
worktree as watcher PID `416958`, log `/tmp/rg55-cmru-gate-watch.log`. The
watcher launches only at memory PSI `full avg10 <= 5`, uses the required
nice/ionice priority, and writes an explicit `RUN_GATE_EXIT` marker; no CMRU
gate container had launched at ruling time.

### RW-125 — 2026-09-14 04:11:23Z — P4 post-merge selftest passes; queue assay-r1

P4's required post-main-merge `selftest` completed on exact tip `b4fb7b1b`
with exit 0: `1160 passed, 3 skipped`, and the separate history record is
`outcome=pass`, `commit=b4fb7b1b1b1c934d9a38f959b889629704dfd31d`,
`history_eligible=true`. The daemon absence warning is expected at this
stage and is not a verdict change. The next required P4 gate,
`./run-gate.py assay-r1`, is queued behind the PSI gate as watcher PID
`425870`, log `/tmp/rg55-p4-final-r1-watch.log`; no new mutation lane was
launched.

### RW-126 — 2026-09-14 04:12:09Z — cap P6 full-suite verification

P6's implementer started its full serial pytest verification in exact
container `cgprofile-p6-full-420454`, with a five-minute timeout and no assay
mutation. The controller verified the command and immediately applied the
standing 3-CPU cap (`NanoCpus=3000000000`). The run's explicit `PYTEST_RC`
marker will be read separately; no additional P6 test container is permitted
until this one exits and the PSI gate permits it.

### RW-127 — 2026-09-14 04:18:24Z — queue the fresh P1 r2 after its history report commit

P1's prior round-1 reviewer report was committed as historical evidence in
`c1830122`; the branch is clean at exact tip
`c18301225be36cc3d727c8bd20136638a6985775`. Because assay identity is per
tree, the old r2 verdict is not reused. A fresh registered `./run-gate.py r2`
was queued behind memory PSI as watcher PID `432284`, log
`/tmp/rg55-p1-r2-watch.log`. It has not launched while the PSI gate is closed;
the exact assay container will be capped immediately when it appears.

### RW-129 — 2026-09-14 04:21:36Z — P1 r2 and CMRU assay launch under the two-lane cap

The PSI gate admitted two queued operations as the MDT workload subsided:
P1's fresh r2 mutation run in exact container
`run-gate-vbpub-r2-435521-1789359672` and CMRU's registered gate's first
assay sub-lane in exact container `run-gate-vbpub-assay-435525-1789359674`.
The controller immediately capped both to 3 CPUs and verified
`NanoCpus=3000000000`. This is the estate's two active container lanes; P6
r2 remains prohibited until one is free. The P4 r1 watcher remains PSI-gated.

### RW-130 — 2026-09-14 04:23:24Z — CMRU advances to coverage under the cap

CMRU's assay sub-lane completed and its registered gate launched the next
coverage sub-lane as exact container `run-gate-vbpub-coverage-437087-1789359745`.
The controller immediately applied and verified the 3-CPU cap
(`NanoCpus=3000000000`). P1's r2 remains the sole active mutation lane; CMRU's
coverage lane is non-mutation and may run beside it.

### RW-128 — 2026-09-14 04:20:56Z — record the operator MDT release outcome without touching its dirty checkout

The operator-owned `cmru.release.log` transaction reached its explicit
`MDT_RELEASE_EXIT=0`. The image build log contains a BuildKit cache-export
warning (`ref layer ... locked ... unavailable`), but the build flow continued,
the required release-flow gate passed (`25 tests, 1 skipped`), the image was
promoted, and the release completed. CMRU then could not rebase the dirty
operator checkout (`cannot rebase: You have unstaged changes`), emitted its
warning, and still returned zero. The controller leaves that checkout and all
operator files untouched; the CMRU release-sync repair remains the remedy for
future dirty-main releases.

### RW-131 — 2026-09-14 04:24:34Z — P6 oracle repair committed and fresh r2 queued

P6's Luna xhigh implementer committed the 34 behavioral oracle tests and the
complete 45-survivor disposition/report as
`0426fd15675997f97febb2dfaf2e219e183f8ea8`. It reports focused `113 passed`,
full `1378 passed, 4 warnings`, `docker wait=0`, `ExitCode=0`,
`OOMKilled=false`, compileall and diff checks green, with production code
unchanged. The branch is clean. Because the test commit changes the judged
tree, the old r2 evidence is invalid; a fresh `./run-gate.py r2` is queued
behind PSI as watcher PID `439173`, log `/tmp/rg55-p6-r2-watch.log`. It will
consume the second mutation slot only after PSI permits launch; P1 remains
the first.

### RW-132 — 2026-09-14 04:27:58Z — CMRU coverage gate fails on four new-path statements

CMRU's registered gate stopped after its coverage sub-lane: `1770 passed,
11 skipped`, but total line/branch coverage was `99.90%` against the required
100%. The coverage JSON identifies only `cmru/src/cmru/transaction.py:1257,
1258,1265,1266` and their five branches, all in the new non-conflict
`abort_result` classification. The accepted real interrupted-rebase fixture
executes in a child process, so those lines do not enter the parent coverage
collection. No merge or release is authorized. Fresh Luna xhigh implementer
Poincare is repairing this with same-process/coverage-aware behavioral oracles
on `cmru-release-dirty-sync`; a new final review and complete gate are required
after its commit.

### RW-133 — 2026-09-14 04:28:59Z — launch and cap P6 fresh r2 in the second mutation slot

As PSI reached `full avg10=3.42%`, the queued P6 watcher launched its fresh
mutation run in exact container `run-gate-vbpub-r2-441342-1789360023` against
quiet tree `0426fd15675997f97febb2dfaf2e219e183f8ea8`. The controller applied
and verified `NanoCpus=3000000000` immediately. P1's
`run-gate-vbpub-r2-435521-1789359672` remains capped at 3 CPUs; these are now
the two active mutation lanes estate-wide. No further mutation container may
launch until one of these reaches a terminal verdict.

### RW-134 — 2026-09-14 04:36:01Z — resume controller and correct the P4 r1 launcher

The controller has resumed the RG-55 close-out. P1 and P6 fresh r2 mutation
runs are active and capped at 3 CPUs in their exact containers. P4's queued
r1 watcher reached the PSI gate but invoked `assay-r1` without its required
comparison base on a worktree with no upstream, so it exited 2 before starting
a gate (`run-gate: lane 'assay-r1' delegates its comparison base; pass --base
REF`). No P4 evidence is accepted from that invocation; it will be relaunched
with `--base main` when PSI and the mutation-slot rules permit. CMRU's gate is
blocked on its committed same-process coverage repair and a fresh review.

### RW-135 — 2026-09-14 04:37:15Z — relaunch P4 r1 with its recorded comparison tree

The failed P4 r1 refusal is superseded by a PSI-gated watcher PID `451699`
running `./run-gate.py --base 186461de assay-r1` from the P4 worktree. This is
the handoff's recorded accepted P2 comparison tree; the watcher will report an
explicit `RUN_GATE_EXIT` marker. It is a non-mutation bare-host gate and may
run alongside P1/P6, subject to the host PSI gate.

### RW-136 — 2026-09-14 04:41:07Z — P4 r1 passes; queue the r3 canary

P4's corrected assay-r1 completed with `PASS`, exit 0, and commit
`b4fb7b1b1b1c934d9a38f959b889629704dfd31d`; the verdict JSON was read in a
separate step. The controller discarded the prior exit-2 refusal. P4's
non-delegating `assay-r3` canary is now queued behind PSI as watcher PID
`481080`, log `/tmp/rg55-p4-final-r3-watch.log`; it is intentionally invoked
without `--base`, which that lane refuses by contract.

### RW-137 — 2026-09-14 04:41:35Z — P4 r3 canary passes

P4's assay-r3 completed under the PSI gate with exit 0. The canary reported
both expected rejection probes (`median-not-mean` and
`median-not-mean-series-stats`) as OK, with `2 rejected, 0 survived`. The
non-mutation P4 gates are therefore green through r3; assay-r2 remains the
last required P4 gate after survivor triage and a quiet judged tip.

### RW-138 — 2026-09-14 04:45:17Z — CMRU coverage repair committed; fresh final review

CMRU's same-process coverage repair is committed at
`700ef5f86ae09cbed1214687cb75e1797c346c30`. The implementer reports focused
release-sync `17 passed, 89 deselected` and full coverage `1,779 passed, 3
skipped; 100.00%` line and branch coverage, with production code unchanged.
Because the test/report commit changes the judged tree, the prior 99.90%
coverage result is not reused. Fresh Luna xhigh reviewer Dirac is reviewing
the exact tip and will commit `cmru-release-dirty-sync-REVIEW-final-3.md`
before the registered gate is rerun.

### RW-139 — 2026-09-14 04:56:18Z — CMRU final review accepts; queue complete registered gate

Fresh Luna xhigh reviewer Dirac accepted CMRU's exact repair tree. The review
report is committed at `b1150e6dd05d279989264ae2eb4486d674cf2076`; it records
the real-hook live probes, full tests, and 100% coverage. CMRU's complete
registered `./run-gate.py gate` is queued behind the two active P1/P6 mutation
containers and the PSI gate; no pre-review evidence is reused.

### RW-140 — 2026-09-14 04:56:49Z — park CMRU gate until a mutation slot is free

The CMRU complete-gate watcher is PID `511432`, log
`/tmp/rg55-cmru-final-gate-watch.log`. It checks host memory PSI and the exact
active P1/P6 `r2` container count before invoking `./run-gate.py gate`; the
initial check observed PSI `4.44%` and two active mutation containers, so it
remains parked. Its terminal `RUN_GATE_EXIT` marker will be read separately.

### RW-141 — 2026-09-14 05:13:48Z — mutation lanes remain live; preserve the queued gate

The controller's fresh process, container, and progress check confirms P1's
exact r2 run is active at 47/252 candidates and P6's exact r2 run is active at
55/484. Both exact containers report `running`, `OOMKilled=false`, and
`NanoCpus=3000000000`; their in-container pytest children are executing. No
`RUN_GATE_EXIT` marker exists for either wrapper. The CMRU complete-gate watcher
PID `511432` remains alive and correctly waits while two
`run-gate-vbpub-r2-*` containers exist or memory PSI exceeds 5%. No judged tree
was switched or committed, and no new lane was launched.

### RW-142 — 2026-09-14 05:21:39Z — queue B097 R1 as a non-mutation lane

The clean B097 tip `0303a24d66c704e03ba10d523f36572a41d0df7a` has its required
non-mutation `./run-gate.py --base main assay-r1` queued in watcher PID
`533996`, log `/tmp/rg55-assay-b097-r1-watch.log`. The watcher performs a
fresh memory-PSI check, runs with `nice -n 19 ionice -c 3`, records
`ASSAY_B097_R1_EXIT`, and caps any exact `run-gate-vbpub-*` container it sees
with zero CPUs to 3 CPUs. Its first check read `full avg10=5.93%`, so it has
not launched yet. P1 and P6 remain the only active mutation lanes.

### RW-143 — 2026-09-14 05:23:31Z — B097 R1 admitted on a detached new-session watcher

The earlier B097 queue shell exited before admission, so it is not treated as
a run. A corrected `setsid` watcher is now confirmed reparented to PID 1 as
PID `534923` in `/tmp/rg55-assay-b097-r1-watch.log`; it admitted the exact
clean tip `0303a24d` after reading `full avg10=0.15%` and launched
`./run-gate.py --base main assay-r1` (run PID `534927`). The assay R1 process
is active, no additional mutation container was created, and P1/P6 remain the
only active mutation lanes. The watcher records its exit marker and caps any
exact gate container it observes.

### RW-144 — 2026-09-14 05:26:15Z — B097 R1 passes; queue the R3 canary

The B097 R1 wrapper `/tmp/rg55-assay-b097-r1-watch.log` recorded
`ASSAY_B097_R1_EXIT=0`, and its verdict JSON independently reports
`outcome=PASS`, commit `0303a24d`, schema 11, and required branch-aware 100%
R1 coverage. The controller then admitted the non-mutation R3 canary from
the same quiet tip after a fresh `full avg10=4.54%` check. Persistent watcher
PID `553845` and run PID `553849` are recorded in
`/tmp/rg55-assay-b097-r3-watch.log`; the canary has no comparison-base
override and has not yet produced its exit marker.

### RW-145 — 2026-09-14 05:26:52Z — B097 R3 canary passes

The B097 R3 watcher recorded `ASSAY_B097_R3_EXIT=0`. Its separate run log
reports both required canaries (`median-not-mean` and
`median-not-mean-series-stats`) rejected and `2 rejected, 0 survived`. R3 has
no verdict JSON artifact by lane design; the wrapper and canary output are the
authoritative evidence. B097's R1 and R3 non-mutation gates are now green;
its registered full gate remains required before merge.

### RW-146 — 2026-09-14 05:29:22Z — keep exact mutation-container caps enforced asynchronously

Because the earlier short-lived cap shell did not survive the terminal
session, the controller started a detached new-session cap watcher PID
`556604`, reparented to PID 1, with log `/tmp/rg55-cap-watch.log`. It launches
nothing; every five seconds it inspects the exact active
`run-gate-vbpub-r2-*` names and applies `docker update --cpus=3` only when an
exact container reports zero CPUs. It exits after P1, P6, and the CMRU gate
watcher have all ended and no r2 container remains. Current P1/P6 caps are
already 3 CPUs.

### RW-147 — 2026-09-14 06:41:59Z — queue B097 full gate and P4 final mutation

B097's required registered `./run-gate.py gate` is parked in detached watcher
PID `659783`, log `/tmp/rg55-assay-b097-gate-watch.log`. It admits only when
memory PSI `full avg10` is at most 5% and fewer than two exact
`tester-unified:local` containers exist. P4's final `assay-r2` is parked in
detached watcher PID `660921`, log `/tmp/rg55-p4-r2-watch.log`; it admits only
when memory PSI is at most 5% and fewer than two exact
`run-gate-vbpub-r2-*` mutation containers exist. Both watchers are reparented
to PID 1 and emit independent exit markers. At this ruling P1 is at
131/252 and P6 at 161/484; both exact containers remain running, not
OOM-killed, and capped at 3 CPUs. No new gate has launched, and no judged
tree was switched or committed.

### RW-148 — 2026-09-14 06:52:15Z — make mutation-budget resumes explicit

The fresh P1 and P6 runs each have a 14,400-second lane budget. Detached
supervisors are now confirmed alive: P1 PID `672449` and P6 PID `673865`.
After the original wrapper and exact container terminate, each supervisor
reads the fresh verdict, resumes only on `BUDGET_EXCEEDED`, verifies the
worktree is clean at its judged commit, switches detached to that exact tree
for the run, and restores its branch afterward. A corrected P4 watcher PID
`675036` will admit only after both supervisors emit their PASS markers; it
normalizes absent counters to zero and is currently parked. No mutation run
has been restarted or duplicated.

### RW-149 — 2026-09-14 07:49:30Z — triage release-log failure and operator backlog edit

Read-only inspection of `cmru.release.log` confirms the prior release
transaction completed its package build, required gate, promotion, and
publication; its final local-main synchronization warning was caused by
unstaged caller-checkout changes (`error: cannot rebase: You have unstaged
changes`). The CMRU repair branch `b1150e6d` has the corresponding dirty-main
preservation and explicit sync-outcome implementation, tests, documentation,
and final adversarial acceptance; its registered full gate remains queued
behind the two active mutation containers. The operator's dirty
`KNOWN_ISSUES_TODO_BACKLOG.md` addendum records a further RG-45 timeout
observation, not a new RG-55 item; it is preserved byte-for-byte and is not
staged or committed.

### RW-150 — 2026-09-14 07:51:37Z — focused CMRU transaction suite remains green

With memory PSI below the launch gate, the isolated CMRU repair worktree ran
`nice -n 19 ionice -c 3 python3 -m pytest cmru/tests/test_release_transaction.py
-q`: 106 passed in 10.93 seconds, exit 0. The worktree remains clean; the
registered tester-unified full gate is still authoritative and remains queued
behind the active P1/P6 mutation containers.

### RW-151 — 2026-09-14 08:27:24Z — correct gate invocation and isolate base-forwarding fix

The CMRU repair gate was launched from its `run-gate-project/` directory. The
first attempt used the repository-root path and exited 127 because that
directory has no `run-gate.py`; the second used the stale lane name `gate` and
exited 2 because the declared lanes are `selftest`, `assay-r1`, `assay-r2`,
`assay-r3`, and `gate-full`. The corrected sequence is running as
`selftest && --base main assay-r1 && assay-r3`, with its job exit marker
preserved separately. A `gate-full --base main` attempt then exposed a real
configuration defect: the conjunction carried no `{base}` token, so run-gate
correctly refused the explicit base before `assay-r1`. A new isolated
worktree, branch `rg55-gate-full-base-propagation`, carries commit `84fab73e`
which adds the token and documents the linked-worktree invocation. Its
targeted propagation test and dry-run passed; a live acceptance is running.
No main files, judged mutation trees, or operator-owned dirty files were
changed by this sidecar work.

### RW-152 — 2026-09-14 08:41:03Z — use shipped assay A5 for P1 recovery

The first P1 `--rejudge-outcome budget_exceeded` recovery attempt exited
before any candidate work because the P1 judged worktree's pre-A5 assay
source rejected that option. This is a judge-tool compatibility boundary,
not a cgprofile verdict: B091/A5 is already shipped in assay 6.2.0. The
recovery will therefore invoke the main checkout's assay 6.2 source while
keeping cgprofile detached at the exact judged tree
`c18301225be36cc3d727c8bd20136638a6985775`; no source commit or mutation
identity changes. The failed attempt's container exited 2 and was removed;
its stale verdict remains untouched until the corrected recovery completes.

### RW-153 — 2026-09-14 08:54:44Z — B097 final non-mutation gates green

On branch `assay-b097` at `0303a24d`, the corrected final gate sequence ran
with independent exit markers and completed green: `selftest` reported 1087
passed and 3 skipped; `--base main assay-r1` reported `PASS`; and `assay-r3`
reported 2 rejected and 0 survived. The supervisor checked memory PSI before
each phase and did not start an R2 mutation campaign. This evidence is
eligible for the assay sidecar's final merge gate once the fresh B092+B098 and
B096 reviews complete.

### RW-154 — 2026-09-14 09:10:41Z — distinguish supplemental and authoritative assay gates

The green B097 sequence recorded in RW-153 exercised the shared
`run-gate-project` consumer lanes (`selftest`, `assay-r1`, and `assay-r3`);
it is supplemental evidence for the branch's run-gate integration, not the
assay package's authoritative `assay/run-gate.py tester-unified` gate. The
correct authoritative gate is running on B096's combined branch at
`51d9701e`, and its outcome is the merge gate for the B092/B098/B096 sidecar.

### RW-155 — 2026-09-14 09:19:20Z — stale shared cgprofile edit ask is cleared by workspace state

The P1 handoff's ask whether the controller may discard the shared
`scripts/cgroup-profiler/cgprofile.py` `HERE`→`DEFAULT_OUT` edit is no longer
active: the shared checkout has no modification to that file. No operator file
was discarded. The only remaining shared-checkout modification is the
operator-owned `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` addendum, which
remains untouched and uncommitted.

### RW-156 — 2026-09-14 09:23:17Z — RG-26 sidecar review requires a real-config oracle

Fresh Luna xhigh review of the isolated `84fab73e` `gate-full --base`
propagation fix accepted the runtime shape, safety, documentation, targeted
tests, and live acceptance probe, but rejected the change because no committed
test reads the shipped `run-gate-project/run-gate.toml`; all existing tests use
synthetic conjunction TOML. The sidecar remains isolated for that proof repair
and is not merged into `main`. RG-59 itself is already implemented on the
checkpointed P4 branch (`b5e4a9c6`, with subsequent false-positive and exit
status repairs), so its redundant sidecar was stopped and removed.

### RW-157 — 2026-09-14 09:30:05Z — B096 authoritative gate is red in pre-existing P25 qualification

The authoritative `assay/run-gate.py tester-unified` run on B096 commit
`51d9701e` passed wheel installation, attestation, schema hard-cut checks,
the 110-test verdict phase, and the self-hosted tester lane, but failed P25's
`declared-base-as-tag` qualification with
`FAIL/COMMAND_FAILED`. The disposable scenario creates a tag on its baseline,
then the assay snapshot-side check appears unable to resolve that symbolic ref
after snapshot materialization. B096 is not merged. A fresh Luna xhigh repair
implementer is dispatched from `assay-B096-BRIEF-2.md`; this is a real gate
failure, not waived evidence.

### RW-158 — 2026-09-14 09:37:54Z — RG-26 duplicate wrapper terminated

The controller's corrected RG-26 gate wrapper was started after the delegated
implementer had already launched the authoritative `gate-full --base main`
run. The duplicate wrapper was terminated by its exact recorded PIDs; the
implementer's original gate was left untouched and remains the only RG-26 gate
evidence. The initial wrapper had already failed before launching because its
working directory was unset; neither wrapper result is evidence.

### RW-159 — 2026-09-14 09:40:03Z — retire the stale P4 launch watcher

The original P4 r2 watcher was keyed to the exhausted P1 supervisor marker,
so it could never launch P4 after P1 recovery. The exact watcher PID was
terminated; no gate or container was affected. A replacement watcher will be
created only after P1 and P6 have terminal mutation evidence and their final
package gates are complete.

### RW-160 — 2026-09-14 10:24:00Z — P1 mutation survivors require new oracles

The exact P1 r2 verdict on `c18301225` was read separately: 252 candidates,
236 killed, 16 survived, 0 budget-exceeded/crashed, overall
`FAIL/MUTANTS_SURVIVED`. A fresh Luna xhigh triage classified 9 survivors as
genuine behavioral gaps (including foreign-DAMON ownership preservation,
orphan DAMON replay, stop-series reporting, and missing CPU-delta input) and
7 as the previously accepted equivalents with line drift. P1 is not green by
classification alone. The nine focused tests were added in `88606ed0`, and a
fresh r2 is running on that exact quiet tip with its container capped at 3
CPUs; the old verdict is not reused as a pass.

### RW-161 — 2026-09-14 10:24:00Z — RG-26 proof sidecar merged

The RG-26 sidecar's final fresh Luna xhigh review accepted commit `e766b75a`
after the shipped `run-gate.toml` construction oracle, focused tests, dry-run,
live `gate-full`, and mutation-of-the-config red checks. It was merged
no-ff into `main` as `6f595186`. The separate controller duplicate gate was
terminated and is excluded from evidence; P4 already carries the production
config fix, and the merged proof will travel with the next run-gate release.

### RW-162 — 2026-09-14 10:32:23Z — discard the stale P1 r2 after coverage failure

The P1 r2 started on `88606ed0` was intentionally stopped before meaningful
judging could occur: the package's preceding r0-r1 gate had 1153 passing
tests but exit 2 because whole-package coverage was 99%, with uncovered
defensive branches in `cgprofile.py`, `serve.py`, and `damon.py`. The exact
P1 tester container `run-gate-vbpub-r2-1155345-1789380973` was captured,
terminated, and auto-removed; its partial `exit 137` is not mutation
evidence. A coverage repair is being added before the next quiet-tip r2.

### RW-163 — 2026-09-14 11:05:52Z — defer P4 until both daemon tracks clear their gates

The controller briefly prepared a mechanical P4 r2 watcher keyed only to P6's
mutation supervisor, then stopped it before it could launch a container. RW-159
requires P4 to wait for terminal mutation evidence and final package gates from
both P1 and P6; that stronger condition remains binding. No P4 assay state was
changed and no additional mutation lane was started.

### RW-164 — 2026-09-14 11:13:41Z — install the verified CMRU release-flow repair

The merged CMRU dirty-main repair (`35e83083`) was built and installed into
`/home/vscode/.venv`; `cmru version` now reports
`5.2.2.dev212+g8df7b01d.d20260914`. Its release preflight remains correctly
fail-closed while this local `main` is ahead of `origin/main`; publishing the
RG-55 commits still requires the operator to synchronize/push `main` first.

### RW-165 — 2026-09-14 11:34:25Z — B096 repair gate remains red at P25

The authoritative B096 gate on repair commit `84baffb4` passed wheel
installation, attestation, schema hard-cut checks, the 110-test verdict phase,
and the self-hosted tester lane, but P25 still failed its
`declared-base-as-tag` scenario with `FAIL/COMMAND_FAILED`. The terminal
failure is not mutation evidence and B096 remains unmerged. A fresh Luna
xhigh repair implementer is diagnosing the preserved disposable-repository
scenario from `assay-B096-BRIEF-2.md`; the existing round-2 review remains
unchanged until a repair is committed and gated.

### RW-166 — 2026-09-14 11:58:53Z — replace an uncheckpointed B096 repair worker

The first fresh Luna xhigh B096 repair worker remained alive after its focused
diagnostic container ended, but produced no filesystem change or checkpoint
after two queued prompts. It was explicitly retired, and a fresh Luna xhigh
successor was dispatched on clean tip `84baffb4` with a bounded reproduce,
repair-or-BRIEF checkpoint contract. P1 and P6 mutation containers remain
untouched.

### RW-167 — 2026-09-14 12:09:43Z — narrow final B096 repair dispatch

The second fresh Luna xhigh B096 repair worker also remained alive for more
than ten minutes without a process, file change, or checkpoint after the
focused diagnostic ended. It was explicitly retired. A final fresh Luna
xhigh worker was dispatched with a narrower contract: capture the exact P25
artifact/reason, make the smallest principled repair with focused tests, or
commit a BRIEF immediately; the full gate is deferred until such a repair is
committed. P1/P6 mutation containers remain untouched.

### RW-168 — 2026-09-14 12:15:23Z — B096 reproduction must run in tester-unified

Halley's checkpoint `99e588e9` established only an environment fact: the
cockpit lacks `/opt/tester-venv`, so its `ERROR/EXEC_FAILED` result was not
evidence about P25. The worker was closed after checkpointing. A fresh Luna
xhigh successor was dispatched to reproduce the exact
`declared-base-as-tag` case inside `tester-unified:local` with both repository
mounts, the governed cgroup parent, and separate container wait/log statuses;
no code repair is permitted before that artifact and pytest evidence is
captured.

### RW-169 — 2026-09-14 12:31:36Z — B096 P25 reproduction clears the base repair

The final fresh Luna xhigh reproduction ran inside the correctly mounted and
governed `tester-unified` container. Docker wait and logs both exited 0. The
scenario resolved `p33-declared-base` to the expected immutable base OID and
R1 passed; the ref-free P22 snapshot was also observed as expected. P25's
`FAIL/COMMAND_FAILED` came from the pinned Topos command itself:
`test_mounted_drill_screen_surfaces_unavailable_damon_controls` timed out
under `topos/tests -q -n auto` after `1 failed, 2922 passed`. This is not an
Assay base-resolution defect, so no product repair is authorized from this
evidence. B096 remains unmerged pending a deterministic Topos qualification
command or repair of that Topos-owned test, followed by a fresh P25 and the
registered gate.

### RW-170 — 2026-09-14 12:35:25Z — retain the operator-owned RG-45 addendum

The shared checkout's only dirty tracked file remains
`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`. Its new 2026-09-12 addendum
records a distinct lane-timeout manifestation of the already-filed RG-45/B078
cross-container contention issue: four `ui_unit` timeouts despite quiet
pre-launch PSI readings, and a direct 714.67-second green suite exceeding the
fixed ten-minute assay budget. This is valid backlog evidence, but it is
operator-owned and outside RG-55's dispatched scope. Leave it dirty and do not
stage, rewrite, or commit it; no RG-55 package adopts it in this wave.

### RW-171 — 2026-09-14 12:43:35Z — P4 round-3 repair is dispatched

The current P4 branch `rg55-followups-run-gate` contains the final exact-tree
R2 PASS and branch gates, but its fresh round-3 adversarial review found two
merge-blocking defects: an exec inflight record can leak when `Popen` fails
synchronously, and the public SPEC/CONSUMERS overview contradicts the shipped
`wait4` child-rusage implementation. A fresh Luna xhigh implementer is
repairing those two findings with focused behavioral tests and documentation;
the existing reviewed mutation evidence remains valid, and no new mutation
lane is launched while P1/P6 occupy the estate's two mutation slots. The
repair must receive a fresh final adversarial review before P4 merge.

### RW-172 — 2026-09-14 12:52:37Z — P6 ordinary resume exhausted; rejudge required

P6's supervisor completed all five ordinary `--resume` attempts. The fresh
verdict was read only after the final child/container exited and remains
`BUDGET_EXCEEDED/LANE_TIMEOUT` on tree `0426fd15`: the durable progress stream
contains 484 candidates with 466 killed, 12 survived, and 6
`budget_exceeded` records. Ordinary resume merged those six placeholders
without executing them, so this is not a final P6 verdict. Assay 6.2.0's
supported `--rejudge-outcome budget_exceeded` continuation is required against
the unchanged judged tree. Its launch is deferred until memory PSI `full
avg10` is at or below 5; P1's one active mutation container remains running.

### RW-173 — 2026-09-14 12:56:15Z — rejudge watcher made session-detached

The first P6 rejudge watcher was started with `nohup` but died during its
initial PSI wait when the controller shell ended; its log contains no launch,
marker, worktree switch, or mutation container, so it is not evidence and no
state changed. The replacement is `setsid`-detached and is the sole P6
rejudge supervisor. It will launch the exact assay 6.2.0 continuation only
after the same PSI/slot gate, then record independent Docker wait/log/remove
statuses and restore the branch.

### RW-174 — 2026-09-14 12:59:32Z — reserve RG-63 for the P5 backlog row

The P5 handoff's transport/watch/placement row was corrected in isolated
commit `ea3c248a` on branch `rg55-p5-handoff-prep`: its title and C1 now use
RG-63, leaving P4's RG-62 flaky-test row unambiguous. This documentation
commit is not merged or dispatched yet; it will be carried into the P5 branch
after P4 and P6 are released, as required by the wave sequence.

### RW-175 — 2026-09-14 13:08:26Z — retire silent P4 review session

The fresh Luna xhigh P4 reviewer was given a bounded review/checkpoint prompt
after five minutes without a report and still produced no filesystem artifact
or response in the following minute. It was explicitly closed. No repository
file, gate, daemon, or mutation state was changed; the final adversarial review
will be performed fresh after P4's post-repair R2 and package gates are green.

### RW-177 — 2026-09-14 13:21:28Z — retire silent P3 readiness sidecar

The read-only Luna xhigh P3 close-out audit sidecar was given a bounded
checkpoint request after it produced no artifact or response. It still wrote
no `/tmp/rg55-p3-readiness-luna.md` and changed no repository, gate, daemon, or
external-worktree state, so it was closed. P3 will be audited directly after
the daemon releases, using the authoritative live-probe and release evidence.

### RW-176 — 2026-09-14 13:16:26Z — queue the authoritative combined assay gate

The accepted B092+B098 implementation is carried by the clean `assay-b097`
tip `0303a24d`, together with B097's liveness identity work. A detached Luna-
policy supervisor `/tmp/rg55-assay-b097-gate-supervisor.sh` now waits for
memory PSI `full avg10 <= 5` and fewer than two exact `run-gate-vbpub-*`
containers, then runs the authoritative `tester-unified` gate with independent
exit markers. It has not launched because PSI is still above the admission
threshold. This is a non-mutation assay gate and does not alter P1/P6's
judged trees; no merge or release is authorized until its job status and assay
verdict are both read separately.

### RW-178 — 2026-09-14 13:46:58Z — bounded Luna P3 preflight sidecar

While P1/P6 mutation supervisors and the combined assay gate remain
asynchronous behind the host-PSI admission gate, a fresh Luna xhigh sidecar
(`01a0a02b-8662-7392-a836-4f2c7f1a0e7e`) was dispatched for a read-only P3
close-out preflight. It must write `/tmp/rg55-p3-preflight-luna.md` before
optional analysis and may not edit the repository, launch gates, inspect
mutation progress, touch dstdns, or alter running processes. No package or
judged-tree state changed; the artifact is preparatory and does not authorize
P3 close-out.

### RW-179 — 2026-09-14 13:51:33Z — stage the P3 report in an isolated worktree

The P3 sidecar stopped at its required discovery checkpoint. To make concrete
progress without touching a judged tree, the controller created the fresh
`rg55-closeout-prep` worktree and staged an explicitly incomplete
`run-gate-WAVE-RG55-REPORT.md` containing the release-evidence matrix, live
probe matrix, DAMON measurement table, adoption-brief constraints, and
close-out checklist. The draft makes no live or release claims and will only
be merged after the authoritative probes and package releases are complete.

### RW-180 — 2026-09-14 13:54:29Z — CMRU build stall diagnosed as host/buildkit pressure, not yet a CMRU defect

The operator-owned MDT `cmru release` remains alive in `docker buildx bake`
step `#59 exporting cache to client directory`, with ample filesystem space.
A bounded `docker buildx ls` returned status 124 after five seconds and
reported the governed `mdt-governed-v10` and `pwmcp-governed-v10` endpoints
unable to become ready; the default builder was the only one reported
running, while an unrelated `buildx_buildkit_keen_mestorf0` container was
restarting with exit 137. This is evidence of Docker/host contention under
the already-prohibitive PSI, not proof of a CMRU code defect. The controller
did not touch the operator job, stop any container, or file a speculative
backlog fix; future CMRU release behavior will be re-evaluated after this
build has a real terminal status.

### RW-181 — 2026-09-14 14:01:45Z — Python's subprocess timeout works in control

The P1 run's persisted header proves the 600-second bound is declared, while
the candidate workers have exceeded it. A low-priority, non-pytest control
using the same cockpit Python runtime and `subprocess.run(..., timeout=2)`
raised `TimeoutExpired` at 2.01 seconds. Therefore this is not a generic
Python timeout failure; it remains an unresolved candidate/runner or host
interaction to diagnose after the live judged run, and no active process was
changed.

### RW-182 — 2026-09-14 14:11:34Z — P1 workers remain live under prohibitive host PSI

The P1 final r2 supervisor and its exact container are still alive. `docker
inspect` reports the container `running` and `OOMKilled=false`; `docker top`
shows two pytest workers running for about 51 minutes, while the append-only
progress stream still ends at candidate 89. At the same observation,
`/proc/pressure/memory` reported `full avg10=60.39` and CPU `full avg10=0.00`.
This strengthens the host-load explanation but does not prove the candidate
path's exact wait point. Keep the judged tree and process untouched; the
existing supervisor remains the authoritative asynchronous run, and the
post-terminal diagnosis must use its final marker, Docker status, and verdict.

### RW-183 — 2026-09-14 14:16:37Z — host saturation confirmed at the Docker boundary

A read-only snapshot of P1's exact container reports `NanoCpus=3000000000`
under `dev-background.slice`, with no container memory cap. The Docker stats
request could not return within its 12-second bound, while the host reported
load averages `357.68 356.33 315.69` and memory PSI `full avg10=61.20`.
This is stronger evidence of host saturation than the worker-local symptom;
it is not evidence of OOM (`docker inspect` still reports `OOMKilled=false`).
No process or container was changed, and no new launch is permitted until the
standing PSI gate clears.

### RW-184 — 2026-09-14 14:17:20Z — no stale RG-55 gate process is consuming the host

The host process audit found only the authorized P1 `python3 ./run-gate.py r2`
among the RG-55/run-gate/assay search terms. The only live RG-55-looking
mutation container is its exact `tester-unified:local` container; P6 and the
combined assay supervisor have not launched containers. Therefore no stale
RG-55 process is a safe cleanup target, and the controller leaves all existing
workloads untouched.

### RW-185 — 2026-09-14 20:48:47Z — assay B092/B098/B096/B097 sidecar is merge-ready

The isolated `assay-b097` tip `0303a24d` has a clean worktree, a fresh
adversarial Luna xhigh ACCEPT for B092+B098, B097 round-2 fix verification
ACCEPT, and an authoritative `tester-unified` PASS at that exact clean tip
(exit 0, revision 41). The registered gate also records the expected
`profile_error` for bare-host lanes under RG-57; it is not a failure. The
controller may merge this sidecar serially onto main and then perform its
versioned release workflow; no dstdns write is authorized.

### RW-186 — 2026-09-14 20:51:29Z — merge B092/B098 sidecar; release awaits origin promotion

The assay sidecar was merged no-ff as main commit `260c4013`, preserving the
operator's dirty run-gate backlog and unrelated MDT host-setup note. The
merged assay changes are clean under their project path and retain the exact
gate/review evidence recorded in RW-185. CMRU's installed release flow
correctly refuses to publish local-only commits while main is ahead of
`origin/main`; no push is authorized in this controller session, so the
assay 6.3.0 publication remains pending origin promotion rather than being
falsely reported as released.

P1's resumable supervisor is now waiting for `memory full avg10 <= 5` and a
mutation-slot count below two before resuming the exact judged tree
`5fd0ef13`; no commit will be made in that detached tree during the run.

### RW-187 — 2026-09-14 20:53:36Z — P1 exact-tree mutation resume relaunched

The host PSI gate cleared at `full avg10=0.00` with one active mutation
container. The detached P1 tree `5fd0ef13` was clean and resumed via the
repository `run-gate.py r2`; its exact container is
`run-gate-vbpub-r2-1804772-1789419205`, immediately updated to 3 CPUs. P6's
rejudge remains the other and only mutation lane. The P1 supervisor records
the child exit and restores the branch only after the verdict is written.

### RW-188 — 2026-09-14 21:34:02Z — merged-assay sidecar review dispatched

A fresh Luna xhigh adversarial reviewer was dispatched in an isolated worktree
against the merged assay lineage, covering B092/B098/B096/B097 and the three
user-facing documents. It is limited to serial targeted checks and live probes
under the PSI gate while P1 and P6 continue asynchronously; it may commit a
red-first product fix plus evidence, or an ACCEPT report. It must not merge,
release, touch dstdns, or alter operator-owned files.

### RW-189 — 2026-09-14 21:35:00Z — triage operator RG-45 backlog addendum

The uncommitted addendum in `KNOWN_ISSUES_TODO_BACKLOG.md` is operator-owned
and remains un-staged. It documents a second symptom of RG-45's already-filed
host-wide contention (the fixed 10-minute assay lane budget, in addition to
vitest's 60-second RPC heartbeat), with a direct green-suite reproduction. It
does not describe a distinct RG-55 defect or a fix that belongs in this wave;
no new row or backport is adopted. The addendum remains available for the
future assay/RG-45 follow-up without committing the operator's dirty file.

### RW-190 — 2026-09-14 21:36:00Z — SPEC-V8 D.6 already landed

The required SPEC-V8 Appendix D.6 note is present on main in
`ciu/docs/SPEC-V8.md`, introduced by the RG-55 P0 planning commit and retained
by the amendment commits. It correctly remains labeled a pending operator
note for the next v8 review round; RG-55 does not rewrite it into a completed
v8 feature or touch the operator's CIU round-4 files.

### RW-191 — 2026-09-14 22:06:56Z — merged-assay sidecar ACCEPT

The fresh Luna xhigh review of the merged assay B092/B098/B096/B097 delta
found no blocker. It ran 439 focused serial tests, 42 documentation tests,
35 anchor checks, and fresh combined/live liveness probes; `git diff --check`
was clean. Its committed report `e8d7a79a` was merged no-ff as
`32690ad8`; no product backport was necessary.

### RW-192 — 2026-09-14 22:08:25Z — P6 rejudge stopped, not mutation-complete

The P6 rejudge container ended at `2026-09-14T21:41:44Z` with exit 4 and
`BUDGET_EXCEEDED/LANE_TIMEOUT`. Its verdict contains all 484 candidate slots,
but 156 remain explicitly `budget_exceeded` placeholders alongside 316 killed
and 12 survived candidates. Therefore P6 has no active container now, but its
r2 evidence is not accepted as fully judged; a true `--resume` mutation pass
is still required. P1 remains the sole active mutation container.

### RW-193 — 2026-09-14 22:09:59Z — P6 true mutation resume relaunched

After P6's placeholder-only rejudge stopped, host memory PSI was
`full avg10=0.03` with one active mutation container. The unchanged P6 judged
tree `0426fd15` was clean and resumed through its repository `run-gate.py r2`
resume path; its exact container is
`run-gate-vbpub-r2-1915324-1789423800`, immediately updated to 3 CPUs. P1 and
P6 now occupy the two allowed mutation slots; no third mutation launch is
permitted until one terminates.

### RW-194 — 2026-09-14 22:19:17Z — structural RG-45 policy and dynamic slots

The operator's directive is adopted for the next structural follow-up: host
scheduling pressure must never be silently interpreted as a product-test
FAIL. A complete functional result must come from a verified framework report
where available; an incomplete/pressure-affected run is infrastructure or
inconclusive evidence requiring retry, never PASS and never a product failure.
Mutation budgets remain operational safety ceilings only; exhaustion leaves
unjudged work to resume and does not classify a candidate.

The live RG-55 controller cap remains two mutation containers while this wave
is in flight. "More mutation slots" is interpreted as replacing that fixed
number in the follow-up with gates-slice capacity admission: sum measured or
derived expected footprints, reserve production headroom, and gate on memory
PSI. No literal third container is launched on this 8-core production host
until that admission path is implemented and proven.

### RW-195 — 2026-09-14 22:20:45Z — structural follow-up sidecars dispatched

Two fresh Luna xhigh sidecars were dispatched in isolated worktrees while P1
and P6 continue: one to carve/implement the assay B078 extension for
pressure-affected structured test results, and one to carve/implement the
run-gate RG-56 capacity-admission seam. Both are forbidden from touching
dstdns, operator-owned files, current mutation trees, merge/release, or full
mutation/gate runs. They must commit either an honest red-first implementation
or a concrete next-wave handoff when a missing cross-package dependency makes
implementation premature.

### RW-197 — 2026-09-14 22:36:31Z — RG-56 design review dispatched

A separate fresh Luna xhigh reviewer was dispatched for the committed RG-56
design-only handoff before it is merged. The review targets admission
arithmetic, missing-vs-empty safety, namespace provenance, loaded slice
verification, PSI semantics, dynamic slot calculation, dry-run/override
boundaries, and the atomic start-side race. It is read-only with respect to
the handoff and cannot touch dstdns or operator-owned files.

### RW-198 — 2026-09-14 22:51:15Z — RG-56 design review BLOCKED

The fresh Luna xhigh review of handoff `36745e9b` returned BLOCKED and was
not merged. It found that the handoff still conflated pre-start admission with
the profiler's `containerid:` session operation, compared raw live medians to
resolved candidate reservations, lacked host-authoritative systemd load facts,
did not reconcile registry generations with actual gates-slice leaves, lacked
footprint provenance/value validation, and left numeric/wire/wait semantics
underspecified. These are design blockers, not implementation polish.

### RW-199 — 2026-09-14 22:51:15Z — RG-56 design repair dispatched

A fresh Luna xhigh repair implementer was dispatched in an isolated worktree,
seeded with the blocked handoff and review. The repair must separate atomic
pre-start reservation from profiling, normalize reservation units, require
host-produced systemd/registry/provenance facts, close numeric and race
semantics, and retain the current two-container RG-55 cap until producer and
acceptance evidence exist. The original reviewer remains alive for fix
verification; no merge or release is authorized yet.

### RW-196 — 2026-09-14 22:23:51Z — contention agnosticism and forward progress

Correction to the controller's earlier wording: RG-55 is not trying to
eliminate scheduler contention. Contention is an allowed, planned operating
condition; the 5-CPU gates-slice cap is an environment constraint. The
structural requirement is that functional test results remain deterministic
and contention-agnostic, with pressure-affected or incomplete runs identified
as infrastructure/inconclusive evidence rather than product outcomes.

Standing operating rule: a long-running test is an evidence dependency, not a
reason to pause development. Keep it detached with a mechanical completion
marker; continue non-overlapping implementation, review, docs, triage, and
fixes in separate worktrees. Once evidence returns, run the fix → focused
tests → gate/review verification cycle and integrate the verified change until
each tracked item can truthfully be marked closed. Never use polling, partial
evidence, or a budget timeout as a closure claim.

### RW-200 — 2026-09-14 22:59:36Z — P35 design review NOT READY

The fresh Luna xhigh review of assay P35 returned NOT READY and was not
merged. It found eight cross-package blockers: no RG-55 daemon-watch kill
receipt, non-durable interruption/reconcile state, no one-winner lifecycle
CAS, an undefined run-gate budget producer, an incomplete execution-journal
grammar, an unrepresentable mixed-claim v12 rollup, open receipt identity and
refusal rules, and inconsistent dependency/scope metadata.

### RW-201 — 2026-09-14 22:59:36Z — P35 design repair dispatched

A fresh Luna xhigh repair implementer was dispatched in an isolated worktree,
seeded with the P35 handoff and its eight-blocker review. It must resolve the
full run-gate/cgprofile-to-assay lifecycle, keep incomplete execution out of
functional and mutation outcomes, and return a clean design checkpoint before
the same reviewer performs fix verification. No merge or release is authorized
for P35 yet.

### RW-202 — 2026-09-14 23:12:49Z — P1 resume leaves two unexecuted candidates

The P1 resume against the unchanged judged tree
`5fd0ef135dbc6ca36d3e0e51d2c0595adc41b004` completed with the actual assay
exit 4 and a separately read verdict of 252 candidates: 243 killed, 7
survived, and 2 `budget_exceeded` placeholders. The seven survivors are
currently mapped to behaviorally equivalent teardown/return predicates (the
four already documented equivalents plus three DAMON invariant-redundant
predicates); this is triage, not closure. The two placeholders were never
executed and make the lane BUDGET_EXCEEDED. P1's judged tree remains clean and
untouched; resume those exact candidates after P6 frees the second mutation
slot, then record the final report and rerun required gates/review on the
final tip.

### RW-203 — 2026-09-14 23:15:01Z — RG-56 repair checkpoint sent to same reviewer

The Luna xhigh RG-56 repair is clean at `ec4445440022c8d05518b83f2b136cfe28d607f7`.
It is design-only and resolves the original admission blockers with a
targetless atomic reservation, normalized `reservation_bytes`, host-produced
systemd/registry/PSI facts, committed footprint provenance, bounded wait and
dynamic `additional_slots`; it retains the current two-container cap until a
producer exists. The original Dewey reviewer was sent the repair for the
required same-reviewer fix-verification round. No merge or implementation is
authorized on this design package yet.

### RW-204 — 2026-09-14 23:26:10Z — CMRU BuildKit failure repair under fresh review

The failed MDT release was diagnosed as two distinct facts: BuildKit was
OOM-killed (exit 137) while exporting its approximately 46 GiB local cache
after the OCI export had completed, and CMRU's old ref-equality diagnostic
incorrectly treated the pre-promotion failure as a successful no-op promotion.
The Luna xhigh repair at `028a26667b155e8330e72a73134413ca44fbefe7` adds a
transaction-local successful-promotion marker, regression coverage, and
operator-facing diagnostics/docs/backlog evidence. Local and tester-unified
coverage gates were reported green by the implementer. A fresh Luna xhigh
adversarial reviewer was dispatched; no merge or release is authorized until
that review accepts the marker state machine and cleanup semantics.

### RW-207 — 2026-09-14 23:40:39Z — CMRU review NOT READY on push/marker crash window

Fresh Luna xhigh review of `028a26667b155e8330e72a73134413ca44fbefe7`
returned NOT READY. The reviewer reproduced that a real remote promotion can
succeed and then fail while writing `.promotion-complete`; the parent would
then classify it as pre-promotion and skip the required revert. The review
also requires README/DESIGN-GUIDE/CONSUMERS synchronization and parent-level
regression coverage for this boundary and for per-project marker isolation.
The BuildKit OOM/EOF diagnosis is accepted as infrastructure, but the CMRU
repair remains unmerged pending a same-reviewer fix round.

### RW-208 — 2026-09-14 23:43:58Z — P35 verification remains NOT READY

Helmholtz's same-reviewer verification of P35 repair `40b2106e3efe91c718b8555c30d38957a4040f5f`
closed F-2, F-3, F-4, and F-6 but found four remaining blockers: cgprofile
pre-kill handshake ownership/scope is unspecified; journal bootstrap ownership
and grammar contradict each other; the receipt nonce is specified as both
32-hex and 64-hex; and the decision/dependency metadata is not
machine-discoverable (`D-449` reference and a lint-invalid `implement-1`
tier). Gibbs was sent these findings for another design-only repair pass.
P35 remains neither accepted nor dispatchable.

### RW-205 — 2026-09-14 23:32:37Z — RG-56 verification remains NOT READY

Dewey's same-reviewer fix verification accepted the repaired demand,
host-fact, registry, manifest, PSI, and basic TOCTOU design, but found four
blocking omissions: D-20 placement/enforcement for bare-host and fallback
exec paths plus manifest-derived Docker caps; owner/lane/project/worktree/
commit/profiler-token/scope binding on `start`; checked aggregate reservation
overflow; and current-loader/wire integration for `admission_wait` and the
exact gates-unit field. The findings were sent back to the same Luna xhigh
repair implementer. RG-56 remains design-only and the two-container cap stays
binding.

### RW-206 — 2026-09-14 23:32:37Z — P35 repair sent for same-reviewer verification

P35's corrected design checkpoint `40b2106e3efe91c718b8555c30d38957a4040f5f`
adds the P36 companion producer handoff and resolves the prior eight blockers
without implementing product code. It explicitly keeps current RG-55
interruption handling report-only until the pre-kill receipt capability is
accepted. The same Helmholtz Luna xhigh reviewer was sent the repair for
fix-verification; P35 is not accepted or dispatchable yet.

### RW-209 — 2026-09-15 00:05:11Z — CMRU promotion recovery repair checkpoint

The CMRU BuildKit/EOF repair was extended locally at candidate commit
`cf2b62c7` (isolated worktree `cmru-buildkit-eof-20260914`). It now atomically
records the fetched `origin/main` SHA, transaction branch, and prepared tip
before every promotion push; failure handling accepts that evidence only when
a changed tip is exactly at `origin/main`, while no-op, malformed, and advanced
remote states fail closed. The completion record is transaction-scoped and is
cleared with the existing marker at project-cycle start and cleanup. README,
SPEC, CONSUMERS, operational recovery docs, changelog, backlog, and a
self-verification report were updated. The focused suite passed 37 tests; the
full local CMRU suite passed 1783 tests/3 skips with 100.00% line+branch
coverage. No Docker-backed release gate or fresh Sol xhigh final review was
run; per operator instruction no subagent was spawned, so this candidate is
not merged or released.

### RW-210 — 2026-09-15 00:09:19Z — operator backlog addendum triaged

The operator's dirty `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` addendum
reports a real dstdns observation: `ui_unit` exceeded its fixed 600-second
lane budget even after low pre-launch PSI readings, while a direct 20-minute
run completed 265/265 tests in 714.67 seconds. This is useful evidence of a
budget/measurement mismatch, but its scheduler-contention wording is not
adopted as RG-55 policy. Host load and contention are allowed and planned;
functional verdicts and mutation outcomes must remain deterministic and
contention-agnostic, while incomplete/pressure-limited measurements are
reported as infrastructure/inconclusive. The operator file remains dirty and
uncommitted; no dstdns files were touched.

### RW-211 — 2026-09-15 00:12:12Z — CMRU crash-revert witness strengthened

The CMRU candidate advanced from `cf2b62c7` to `5d4b79e0` with an additional
behavioral assertion: after the completion-marker write fails following a
real remote push, the same source revert operation used by the parent succeeds
and removes the promoted file from `origin/main`. The focused promotion set
remains green. This is still an isolated, unmerged candidate pending the
requested Sol xhigh final review and the Docker-backed release gate.

### RW-212 — 2026-09-15 00:14:26Z — RG-56 corrected successor checkpoint

The RG-56 admission design sidecar has a newer corrected successor at
`92a2c09717e5280463564d3c1c62fe32a4ac6775` in worktree
`.worktrees/rg56-admission`. Its report addresses the four residual design
findings from RW-205 (mode-specific D-20 placement and caps, authenticated
owner/scope binding, checked aggregate overflow, and current-loader/wire
integration). This is a design-only candidate; no producer implementation,
acceptance gate, or fresh adversarial verification is recorded, so it remains
unaccepted and unmergeable.

### RW-213 — 2026-09-15 00:14:26Z — P35 corrected successor checkpoint

The P35 execution-interruption design sidecar has a newer corrected successor
at `6d34f0d7b40ad777c12460e16b60d0022b3589fc` in worktree
`.worktrees/assay-b099-p35-repair`. Its report claims closure of the four
residuals from RW-208 and adds the machine-discoverable P36 and cgprofile P07
companion packets. It remains design-only and unaccepted: no implementation,
producer gate, or fresh adversarial verification is recorded, so no dispatch,
merge, or release follows from this checkpoint.

### RW-214 — 2026-09-15 00:22:31Z — CMRU candidate non-mutation gates green

The isolated CMRU promotion-recovery candidate `5d4b79e0` passed the real
tester-unified assay-r1 lane and the independent assay-r3 canary lane. The
candidate remains clean and unmerged. The required fresh Sol xhigh final
review and any release mutation gate have not run; the controller is not
claiming merge or release readiness while subagent creation is disabled by
the operator.

### RW-215 — 2026-09-15 00:24:58Z — P1 exact-tree mutation resume relaunched

P1's clean judged tree `5fd0ef135dbc6ca36d3e0e51d2c0595adc41b004` was
rechecked before launch. With P6 as the only existing mutation container and
memory PSI `full avg10=0.06`, P1 was resumed in the second permitted mutation
slot. Its exact container is
`run-gate-vbpub-r2-2222080-1789431885`; Docker was immediately updated and
verified at `NanoCpus=3000000000`. The run is detached; its explicit completion
marker is `/tmp/rg55-p1-resume.log` (`P1_RESUME_EXIT=`), and no commit may be
made to the judged tree until the verdict is read separately.

### RW-216 — 2026-09-15 00:37:47Z — P1 budget placeholders rejudged explicitly

The plain P1 `run-gate.py r2` resume completed with exit 4 but merely merged
the prior 252 records (`resumed_total=252`), leaving the two
`budget_exceeded` placeholders untouched. This is because P1's embedded assay
source predates `--rejudge-outcome`; the attempted flag was refused before
execution. A properly mounted tester-unified run then used a temporary copy of
the exact resume state with only those two records omitted. It rejudged the
same tree `5fd0ef13` from `00:31:13Z` to `00:34:19Z`, producing 252 total,
245 killed, 7 survived, 0 budget-exceeded, 0 crashed, exit 1. The seven
survivors are the already documented behavioral equivalents; the state copy's
new records were copied back to the worktree's resume store. The missing
consumer forwarding of `--rejudge-outcome budget_exceeded` is a tooling
follow-up, not silently treated as a green P1 mutation verdict.

### RW-217 — 2026-09-15 00:37:25Z — P4 post-repair mutation gate launched

P4's clean post-repair tip `c8f1654cc371c09e78faa6bf66feaf2aaf2e1a22`
passed selftest, assay-r1, and assay-r3. With P6 as the only other active
mutation run, P4's required fresh `assay-r2 --base main` was launched under
the second mutation slot with `nice -n 19 ionice -c 3`. P4's declared R2
environment is bare-host, so no tester-unified container is expected for this
run; its completion marker is `/tmp/rg55-p4-r2.log` (`P4_R2_EXIT=`). No HEAD
movement or worktree edit is permitted while it judges.

### RW-218 — 2026-09-15 00:40:21Z — P1 final non-mutation gates green

On unchanged judged tree `5fd0ef13`, P1's final r0-r1 gate passed with 1195
tests and 100% line plus branch coverage. Its r3 tester-unified canary lane
also passed with 7/7 canaries rejected and 0 survived; the container was
immediately verified at `NanoCpus=3000000000`. P1 now has terminal mutation
records and green final non-mutation evidence; the seven mutation survivors
remain the documented equivalent dispositions. No package merge, release, or
daemon start has occurred.

### RW-219 — 2026-09-15 02:12:50Z — P4 terminal mutation and final gates

P4's required fresh R2 on clean tip `c8f1654cc371c09e78faa6bf66feaf2aaf2e1a22`
reached a separately read terminal PASS: 59/59 candidates killed, 0 survived,
0 equivalent, 0 budget-exceeded, and 0 crashed. The lane is bare-host, so no
mutation container was expected. Its launch snapshot had stale/high host PSI
(`full avg10=10.57%`); the run was admitted before that reading was observed,
and the later in-run condition is disclosed as host contention/infrastructure
evidence, not a product verdict. Main also advanced after the synthetic snapshot;
the committed judged tree remained unchanged throughout the run.

On the unchanged P4 tree, `selftest` passed with 1161 passed and 3 skipped,
`assay-r1` passed with exit 0, `assay-r3` passed with 2/2 canaries rejected and
0 survived, and `doctor` exited 0 with 8 OK, 2 warnings, 2 skips, and 2 info.
The r1 artifact reports zero considered changed executable lines because the
merge tip's first-parent resolution selects the pre-existing P4 tree; this is
not treated as package coverage proof. The substantive package evidence is the
terminal R2 result and selftest. P4 is ready for the required fresh Sol xhigh
review; no package merge, release, or daemon action is authorized by this
ruling.

### RW-220 — 2026-09-15 02:18:29Z — P6 retry terminal; asynchronous-gate development policy

P6's same-tree resume re-ran the two pending candidates and killed both. The
separately read terminal verdict now aggregates 484 candidates as 465 killed,
14 survived, 0 crashed, 0 equivalent, and 5 `budget_exceeded`, so P6 remains
incomplete. The five placeholders are the two `summary.py` candidates that
exceeded the 600-second per-candidate ceiling and three candidates cut when
the 4-hour lane deadline expired; this is not a green mutation verdict and no
P6 merge/release follows. The P1 mutation lane remains active.

Operating principle for this wave: a long-running verification gate is an
asynchronous evidence dependency, not a development mutex. When implementation
and targeted tests provide actionable feedback while only long gates remain,
continue isolated test/fix cycles and preserve the work in commits. A fix that
changes a judged tree invalidates that tree's mutation evidence; backport or
rebase it to the final tip and rerun the affected gate before marking the item
closed. Never convert partial, stale-tree, or budget-truncated evidence into a
passing checkmark.

### RW-221 — 2026-09-15 02:30:30Z — P6 fresh R2 after oracle repair

P6's timeout-oracle repair is committed at `8076246c3d365df04ecdd1d2f041ada75c081b40`.
The package's targeted `tests/test_summary.py tests/test_serve.py` suite passed
143/143. Because the repair changes the judged tree, all earlier P6 mutation
records are invalid for closure. A fresh detached R2 was launched at this
quiet tip as PID `3403254`, with the exact container
`run-gate-vbpub-r2-3403254-1789439415`; Docker reports it running under
`dev-background.slice` with `NanoCpus=3000000000`. Its completion marker is
`/tmp/rg55-p6-r2-fresh.log` (`P6_R2_FRESH_EXIT=`). P1 remains the other active
mutation lane. No commit may be made to the P6 worktree while this run judges.

### RW-222 — 2026-09-15 02:44:42Z — manual Sol xhigh final-review packet written

Because the controller session cannot create the required Sol runtime, a
manually runnable final adversarial review/repair packet was written at
`run-gate-project/nyxloom-trove/reports/run-gate-P55-sol-final-review.md`.
It is parameterized for one fresh Sol xhigh session per release-blocking
target (P1, P4, P6, and the related CMRU release-recovery candidate), and gives
Sol explicit authority to make scoped fixes, tests, docs, and commits while
forbidding merge/release, dstdns, and operator-owned files. It includes the
open gate, release-order, carrier, footprint, RG-56, and backlog questions,
the required oracle anti-patterns, and the mechanical BLOCKED rule.

The isolated RG-56 implementation sidecar also received the order-independent
estate-pairing test fix at `84341e75`; its full local run-gate suite then passed
`1101 passed, 3 skipped`. RG-56 remains future-wave work and is not merged.
P1 and P6 R2 mutation processes remain active; no package merge or release is
authorized until their evidence and a genuine Sol xhigh review are present.

### RW-224 — 2026-09-15 02:50:26Z — restore passive P6 terminal watcher

The P1 and P6 mutation processes and exact containers were rechecked alive.
The previously created P6 marker watcher had exited without a terminal marker,
so it was relaunched as a detached `setsid` shell watcher PID `3457446`.
It only waits for `/tmp/rg55-p6-r2-fresh.log` to contain
`P6_R2_FRESH_EXIT=` and writes a UTC marker; it does not poll candidate
progress, restart the gate, or alter the judged worktree.

### RW-225 — 2026-09-15 02:51:49Z — semantic pause checkpoint

The controller reaches a coherent pause point. P8, P2, and P7 are shipped;
P4 has complete mutation and non-mutation evidence but awaits the required
fresh Sol xhigh review; P1 and P6 are still judging fresh exact trees in their
two permitted mutation slots; P5 is prepared but correctly waits for P4/P6;
CMRU and RG-56 remain isolated candidates. The P5 handoff correction, manual
Sol packet, RG-56 test repair, watcher, and durable memory updates are landed.
The shared checkout still contains only the two operator-owned dirty paths
listed in the handoff.

The next continuation action is mechanical: when a P1/P6 terminal marker is
present, read the process exit and assay verdict separately, verify tree
identity, triage every survivor or placeholder, and rerun only the required
gates. For P4 or CMRU, a manually launched fresh Sol xhigh session may review
or repair the isolated candidate immediately, but it must not merge/release.
No further controller mutation or tree edits are authorized at this pause.

### RW-226 — 2026-09-15 02:57:59Z — clarify manual Sol launch contract

The manual reviewer returned `BLOCKED` because its target was unset and the
runtime identified as Codex/GPT-5 rather than a verifiable Sol xhigh route.
The review packet now requires a literal `REVIEW_TARGET=P4` (or exactly P1,
P6, or CMRU) in the pasted prompt and names the required Sol route as
`gpt-5.6-sol` at `xhigh`. It explicitly states that prompt text cannot turn a
Codex runtime into Sol; if the client cannot expose that route, the reviewer
must remain blocked before repository work. No package tree was touched.

### RW-223 — 2026-09-15 02:47:19Z — correct P5 backlog identity before dispatch

The future P5 handoff incorrectly reused RG-62, which belongs to P4's flaky
test follow-up. Its new transport/watch/placement row and all P5 close-out
references now consistently use RG-63. This is a handoff-only correction; P5
still cannot dispatch until P4 and P6 are merged, and no current mutation tree
was touched.

### RW-227 — 2026-09-15 03:00:43Z — make path-based Sol handoff invocation explicit

The operator correctly noted that `REVIEW_TARGET=P4` does not by itself tell
a manually started session where the review contract lives. The packet now
opens with an exact invocation block: select the Sol xhigh route, send the
literal target line, explicitly read
`/workspaces/vbpub/run-gate-project/nyxloom-trove/reports/run-gate-P55-sol-final-review.md`
in full, and follow it. Missing or invalid targets remain a mechanical
`BLOCKED`; no package tree was changed.

### RW-228 — 2026-09-16 11:15:35Z — P4 Sol acceptance and no-ff landing

The fresh Sol xhigh P4 review artifact
`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P4-REVIEW-round7.md`
is `ACCEPT` for reviewed product/test/docs tree
`b2eb633e7ed2574d4752e6b3e406446bf03a8321`; its administrative review record
is `d8434fe503377f0de4d0857ef9bee0b61e435499`. The reviewed tree had
1,201 passed and 3 skipped, 274/274 changed lines, 96/96 changed branches,
R1 PASS, R2 85/85 killed with no other buckets, R3 canaries rejected,
gate-full PASS, and doctor zero failures. No additional review was required
because no product bytes changed after that acceptance.

P4 was merged onto the then-current `main` with `--no-ff` as merge commit
`3baffc7493a63722697c5afb06fd0f5df0d65868`. The remaining
`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` RG-45 addendum is unchanged,
operator-owned evidence (as ruled in RW-60/RW-97/RW-170/RW-189/RW-210): it
was preserved with a path-scoped stash across the merge and restored
byte-for-byte, and remains dirty and uncommitted. No operator-owned file was
committed.

### RW-229 — 2026-09-16 11:25:20Z — run-gate 23.8.0 released and installed

After the P4 merge, committed `main` was pushed to `origin/main` so CMRU's
local-ahead safety precondition was satisfied. `cmru release --project
run-gate-project --set-version 23.8.0 --allow-uncommitted` completed its
isolated transaction: the release selftest gate passed with 1,201 passed and
3 skipped in 230.84 seconds; CMRU promoted the prepared release commit,
tagged and published `run-gate-v23.8.0`, and published the wheel with SHA256
`197a600a13ba064702f15347701cea323cbdaf03030201e731e87e34194439af`.
The wheel was installed into `/home/vscode/.venv`; `run-gate --help` reports
`run-gate rev 42`. CMRU could not synchronize the caller checkout afterward
because the operator-owned RG-45 backlog addendum remained dirty; that file
was preserved and restored without staging or committing it. Local `main` was
then fast-forwarded to the promoted release commit and remains equal to
`origin/main`, with only that addendum dirty.

### RW-230 — 2026-09-16 12:14:10Z — P1 oracle repair and exact-tree R2 relaunch

The P1 mutation verdict at `5917d3628e750fe3cad613056e8eb4eccac3e949`
contained three survivors and no budget placeholders. Source-call-path
triage identified the pooled-session teardown survivor at `lib/damon.py:441`
(`False -> True`) as a genuine missing behavioral oracle: the owned pooled
kdamond must be powered off before the pool shrinks the registry, while a
foreign pre-existing kdamond remains on. The two other current survivors
(`lib/damon.py:325` and `:385`) remain candidates for explicit equivalence
justification after the new run.

Luna added the behavioral regression test and committed it as
`8df62f628b20c6280574aef8f6e76a53f9d00e35`; the targeted P1 DAMON suite passed
81 tests. Because assay identity is per tree, the prior R2 evidence is not
used for closure. A fresh R2 was launched against that exact clean tip as
`run-gate-vbpub-r2-1708962-1789560768`, immediately capped at 3 CPUs in
`dev-background.slice`.

P6's unchanged exact tree `8076246c3d365df04ecdd1d2f041ada75c081b40` had no
active process, so its required resume was launched in the second permitted
mutation slot as `run-gate-vbpub-r2-1710318-1789560817`, also immediately
capped at 3 CPUs in `dev-background.slice`. Both detached jobs have terminal
markers and are checked no more often than the operator's 20-minute interval.

### RW-231 — 2026-09-16 12:33:38Z — temporarily widen the background gate envelope

The operator confirmed that this host is currently otherwise idle and
authorized a temporary runtime widening while RG-55 completes. Through a
short-lived root container using the host system bus (no host PID, network, or
cgroup namespace), `dev-background.slice` was set to the already-loaded
`dev.slice` envelope: `CPUQuota=700%`, `CPUWeight=100`,
`MemoryHigh=12348030976`, `MemoryMax=16642998272`, and `IOWeight=100`.
Read-back through systemd and the host cgroup filesystem agrees:
`cpu.max=700000 100000`, `io.weight=default 100`, and empty `io.max`; there
are no bandwidth/IOPS limits. This is runtime-only and does not widen the
parent `dev.slice` or remove the required 3-CPU cap from an individual gate
container. Restore the original child values after the wave:
`CPUQuota=500% CPUWeight=20 IOWeight=10 MemoryHigh=5368709120
MemoryMax=8589934592`.

The P1 exact-tree retry container `run-gate-vbpub-r2-1708962-1789560768`
exited `1` with `OOMKilled=false`; its R2 attempt failed in the R0 baseline
on the nondeterministic `test_new_run_id_is_unique_even_for_the_same_instant`
(49 unique IDs of 50), not in a mutation candidate. P6 remains the sole live
mutation container, `run-gate-vbpub-r2-1710318-1789560817`; no progress poll
is made before the 20-minute interval.

### RW-232 — 2026-09-16 12:37:43Z — relaunch P1 after collected baseline failure

The first retry invocation was initially made from `run-gate-project/`, whose
nearest configuration declares only `selftest`; it therefore exited before
starting a container. The correct cgroup-profiler symlink entrypoint was then
run from `scripts/cgroup-profiler/`. Its first invocation collected the old
exited P1 container instead of starting a replacement. `--fresh` was used only
against that exact exited name and launched
`run-gate-vbpub-r2-1754081-1789562243` for the unchanged clean tree
`8df62f628b20c6280574aef8f6e76a53f9d00e35`; it was immediately verified at
`NanoCpus=3000000000` with `CgroupParent=dev-background.slice`. P1 and P6
are now the two allowed mutation slots; neither will be polled before the
20-minute interval.

### RW-233 — 2026-09-16 13:50:48Z — P5 v1.1 client checkpoint

Controller Luna xhigh completed the isolated P5 run-gate client implementation
and documentation checkpoint at `4d12c430b421bd32f961112bd82e651b5a60c981`.
The full local `tests/test_run_gate.py` suite passed 1,116 tests with 3
skipped; the ended-daemon-watch regression is included. P5 remains unmerged
and unreleased pending the post-P6 ordering and the required fresh Sol xhigh
review. The final-review packet must target P5 explicitly; the assay
source-backed 6.3 worktree remains operator-owned and untouched.

### RW-234 — 2026-09-16 13:56:49Z — mutation-lane wakeup check

At the first post-checkpoint wakeup beyond the 20-minute interval, both
mutation containers are still running and each is capped at 3 CPUs in
`dev-background.slice`: P1 `run-gate-vbpub-r2-1754081-1789562243` (tree
`8df62f628b20c6280574aef8f6e76a53f9d00e35`) and P6
`run-gate-vbpub-r2-1710318-1789560817` (tree
`8076246c3d365df04ecdd1d2f041ada75c081b40`). `docker top` shows assay and
pytest processes in both containers; neither current run has a terminal
verdict yet. The visible `verdict-r2.json` files are the prior completed
attempts and were not treated as evidence for these live runs. No progress
stream was polled.

### RW-235 — 2026-09-16 14:07:40Z — sequence P5 after source-backed assay 6.3

The operator-owned `assay-source-backed-20260916` worktree contains the
committed removal of internal assay 6.1.1 artifact pins, including the
run-gate-project consumer. This is the authoritative dependency path for the
remaining RG-55 run-gate releases: do not release the P5 client from its
stale-pyz branch and thereby create a second mandatory re-vendoring cycle.
After the operator's assay 6.3 release is published, merge the source-backed
consumer changes with P5, reconcile the revision/CHANGES and documentation,
and rerun the full required gates and mutation evidence on that resulting
tree. The controller will not edit or commit the operator's worktree.

### RW-236 — 2026-09-16 14:09:53Z — land SPEC-V8 Appendix D.6

Appendix D.6 in `ciu/docs/SPEC-V8.md` was changed from a pending note to a
landed RG-55 forward-compatibility constraint. It records that a future v8
gate must consume RG-55's profiler registry and contract, not invent a second
profiling or admission vocabulary. This documentation close-out is separate
from the later v8 implementation work and does not touch the protected CIU
round-4 files.

### RW-237 — 2026-09-16 14:31:23Z — P5 defensive coverage checkpoint

The P5 worktree's changed-line coverage additions are committed as
`0f40eca7` on the exact P5 branch. The focused placement/admission regression
set passes 44 tests. A full P5 selftest was launched asynchronously against
that tree with an explicit terminal marker at
`/tmp/rg55-p5-selftest-coverage2.log`; its result is not inferred from the
launcher. No mutation lane was restarted or altered, and the operator-owned
assay source-backed worktree remains untouched.

### RW-238 — 2026-09-16 14:34:02Z — resume P6 after its budget boundary

The P6 mutation attempt against tree `8076246c3d365df04ecdd1d2f041ada75c081b40`
ended at `14:28:41Z` as `BUDGET_EXCEEDED/LANE_TIMEOUT` (exit 4), so it is not
complete evidence. The old exact container was collected by the first resume
invocation; a second invocation with `--fresh` then started
`run-gate-vbpub-r2-2020304-1789569221` at `14:33:41Z`. It was immediately
capped at three CPUs and verified under `dev-background.slice`. Assay's
persisted resume/progress state remains the source of truth; P1 remains the
other active mutation lane.

### RW-239 — 2026-09-16 14:40:02Z — fresh P5 full selftest after watch coverage

P5's in-process daemon-watch transition tests now pass four-for-four, and the
fixture correction is committed as `aa9d4a20` on top of the earlier coverage
commit `0f40eca7`. A fresh full `run-gate.py selftest` was launched from that
quiet P5 tree with an explicit terminal marker at
`/tmp/rg55-p5-selftest-coverage3.log`; no result will be inferred from its
launcher. This is a non-mutation validation lane; P1 and P6 remain the only
mutation slots in use.

### RW-240 — 2026-09-16 14:44:38Z — P5 ended-watch repair checkpoint

The P5 exec-carrier profiler watch had a substantive safety gap: the lane
loop retried only an idle watch, so a watch whose reader thread had ended could
be retained silently. The repair checks `ended()` as well as `idle()`, reports
the distinct state, and reattaches once before using the progress fallback.
Four focused regression tests pass, and the implementation plus tests are
committed on P5 as `26e6f344`. Any earlier P5 selftest predating this commit is
invalid evidence and must be rerun from the quiet new tree.

### RW-241 — 2026-09-16 14:48:37Z — P6 second resume hits another budget boundary

The resumed P6 R2 run on the unchanged judged tree
`8076246c3d365df04ecdd1d2f041ada75c081b40` terminated with exit 4. Its
verdict records all 484 candidates in the mutation set: 467 killed, 15
survived, no crashed or equivalent candidates, and 2 `budget_exceeded`
placeholders. The lane therefore remains incomplete; the two placeholders
must be resumed from this same tree before survivor triage and final gates.

### RW-242 — 2026-09-16 14:49:28Z — resume P6's two budget placeholders

The P6 worktree was clean and detached at the unchanged judged tree
`8076246c3d365df04ecdd1d2f041ada75c081b40`. A third detached `r2 --fresh`
resume was launched as PID `2071035` with assay's persisted resume/progress
state. Its exact container is `run-gate-vbpub-r2-2071035-1789570162`; it is
running under `dev-background.slice` with `NanoCpus=3000000000`. P1 remains
the other active mutation lane. No P6 tree mutation is permitted until this
run reaches a terminal verdict.

### RW-243 — 2026-09-16 14:50:56Z — source-backed assay integration preview

A read-only `git merge-tree` preview of P5 `26e6f344` with the operator's
assay-source-backed branch `37d7adcc` (common base
`0c0d55e42f2589be6378706a7f29bf988f5f2bec`) reports real content conflicts in
`run-gate-project/run-gate.py`, `SPEC.md`, and `CHANGES.md`; the other
run-gate consumer documents merge mechanically. The source branch also owns
protected `nyxloom/` and other estate-wide consumer changes, so it must not be
merged wholesale by this controller. After assay 6.3.0 is published, reconcile
the source-backed consumer commits into the final P5 tree, preserving the P5
implementation and its evidence, then rerun all affected gates on the final
quiet tree.

### RW-244 — 2026-09-16 14:52:20Z — refresh P5 final-review packet

The tracked Sol review packet now names P5 tree `26e6f344` and controller
ruling RW-243, and records that the ended-watch repair's four focused tests are
not a substitute for the pending fresh full selftest. This packet remains
provisional until the assay 6.3 source-backed integration produces the final
quiet review tree.

### RW-245 — 2026-09-16 15:32:34Z — P5 ended-watch repair fully covered

P5 added the bare-host ended-reader repair and behavioral coverage commits
`8dcb3010`, `92cc8013`, `c3dba531`, `984985ae`, and `8823dca8`. From the final
quiet P5 tip, `run-gate.py selftest` passed 1,287 tests with 3 skips; changed
executable coverage is 642/642 lines and 276/276 branches, with explicit
`SELFTEST_EXIT=0`. The daemon-unavailable warning is expected because this
worktree's daemon is not deployed. P5 evidence is still provisional until the
operator's assay 6.3 source-backed integration is reconciled.

The P6 third resume on the unchanged judged tree
`8076246c3d365df04ecdd1d2f041ada75c081b40` ended at `14:51:46Z` with the same
complete candidate accounting but 2 `budget_exceeded` placeholders (467
killed, 15 survived, 0 crashed/equivalent), exit 4. It remains incomplete;
the next resume must use the future source-backed assay release rather than
count these placeholders as judged. P1's exact container remains running.

### RW-246 — 2026-09-16 15:38:03Z — P5 non-mutation gates green

On quiet P5 tree `8823dca820cf6ffc6520da57663f8b7424f1ce35`, assay-r1 was
first correctly refused without a comparison base because the linked worktree
has no upstream; the explicit `--base main` rerun passed, with its verdict
artifact recorded under the P5 worktree's `.assay/`. Assay-r3 passed with both
canaries rejected and zero survivors. `doctor` exited 0 with 8 OK, 2 warnings,
0 failures, 2 skips, and 2 informational checks. The warnings are the known
linked-worktree host-lane git view and the not-running cockpit daemon; no
socket-carrier live acceptance evidence exists yet. P5 R2 mutation evidence
is still outstanding and must be run after source-backed assay integration.

### RW-247 — 2026-09-16 15:46:06Z — P5 gate-full green

The P5 `gate-full --base main` wrapper passed on the quiet tree
`8823dca820cf6ffc6520da57663f8b7424f1ce35`: nested selftest passed 1,287 with
3 skips and 642/642 changed lines plus 276/276 branches; nested R1 passed;
R3 rejected both canaries with zero survivors; and the wrapper exited 0. The
daemon-absent rusage warning remains disclosed. This does not replace the
pending R2 mutation run or the fresh Sol review, and source-backed assay 6.3
integration will require this gate again on the final tree.

### RW-248 — 2026-09-16 15:49:38Z — P5 R2 asynchronous launch

P5 R2 has been launched on quiet tree
`8823dca820cf6ffc6520da57663f8b7424f1ce35` with `--base main`, low-priority
execution, and the required resume/progress mechanics supplied by run-gate.
The first shell-background attempt died before writing any output or marker;
a 10-second foreground probe reached the assay command, and a second launch
through a detached session leader is now running as Python PID `2275238`.
Its explicit result marker is `/tmp/rg55-p5-assay-r2-current.log`.
This run is intentionally provisional: integrating the operator's assay 6.3
source-backed changes into P5 will invalidate its tree identity and require a
fresh final R2 if the tree changes.

### RW-249 — 2026-09-16 15:56:32Z — correct the canonical P5 handoff

The canonical P5 handoff had retained two stale references: it named P4's
RG-62 flaky-test row as P5's row, and it still described the superseded
wait-then-proceed admission sketch. It now consistently reserves RG-63 for
P5 and describes the corrected RG-56 atomic targetless reservation,
target-bound start barrier, fail-closed facts/capacity checks, and placement
refusal safety rule. The scoped documentation commit is `9c6a044c`; the
canonical Sol packet was synchronized to RW-248 in `bf5217bc`. No judged tree,
operator-owned assay-source worktree, running mutation process, or assay
record was changed.

### RW-250 — 2026-09-16 16:03:49Z — P1 survivor repair and fresh R2 launch

P1's exact-tree R2 verdict for `8df62f628b20c6280574aef8f6e76a53f9d00e35`
was terminal and complete: 250 candidates, 248 killed, 2 survived, 0
equivalent, 0 budget-exceeded, and 0 crashed; outcome
`FAIL/MUTANTS_SURVIVED`, exit 1. `damon.py:325` (`Gt->GtE`) is accepted as
equivalent because `current == expected_end` implies `current > baseline` for
every reachable pool release. `damon.py:385` (`False->True`) was a real
oracle gap: failed second acquisition could release a first session's live
constructor index. The red-first regression failed under that exact mutant
and passed restored; `test_damon.py` then passed 82 tests.

The regression is committed as `8cc740a2`; the P1 LOG/REPORT receipt and
triage are committed as `4845a58a`. Those commits invalidate the old R2 for
release, so a fresh R2 was launched on quiet tree `4845a58a` as Python PID
`2389540`, exact container
`run-gate-vbpub-r2-2389540-1789574615`, under `dev-background.slice` with
`NanoCpus=3000000000`. P5 remains the other mutation lane. No P1 merge,
release, or Sol review is authorized from the old result.

### RW-251 — 2026-09-16 16:06:48Z — refresh P3 release evidence for P4

The isolated P3 close-out preparation report now records the already-shipped
run-gate 23.8.0 evidence: annotated tag object
`b986a2fc8be585654f73ca0657b1528d656d88cd` peels to commit
`409439c15132724d36ffc8db61a1324dac542585`, `/home/vscode/.venv` reports
23.8.0, and `run-gate --help` prints rev 42. The preparation commits are
`9deca699` and its tag-object correction `f6c51ab7`. No mutation lane was
queried or changed.

### RW-252 — 2026-09-16 16:07:49Z — mark assay 6.2 evidence as interim

The isolated P3 close-out draft now labels its assay 6.2.0 tag and installed
distribution receipt as interim. The final RG-55 release evidence depends on
the operator's assay 6.3 source-backed release, so the draft no longer
silently presents 6.2 as the final assay state. The preparation commit is
`fd345c38`; no mutation lane was queried or changed.

### RW-253 — 2026-09-16 16:18:30Z — stage P5 source-backed Assay reconciliation

While the operator's assay 6.3 source-backed worktree remains the dependency
of record, a separate P5 reconciliation worktree was created from the judged
P5 tip `8823dca820cf6ffc6520da57663f8b7424f1ce35`. The selected run-gate
consumer and tester-unified changes from the operator's source branch were
applied with P5's transport/watch/placement/admission behavior retained.
Expected `run-gate.py` overlaps were resolved explicitly. The isolated local
run-gate suite passed `1184 passed, 3 skipped` (wheel-toolchain skip only),
exit 0, and the checkpoint is commit `f80608b9` on
`rg55-p5-assay63-reconcile`. This is preparation only: the final assay 6.3
tree, tester image gate, final P5 mutation run, and Sol review remain pending.
No mutation lane was queried or changed.

### RW-254 — 2026-09-16 16:22:29Z — classify `cmru.release.log`

The previously referenced `/workspaces/vbpub/cmru.release.log` was inspected.
It contains only 59 lines of the `cmru release` usage screen, with no release
transaction, subprocess, gate, or error record. It is therefore insufficient
evidence for a release defect; no speculative cmru change is made. The
authoritative RG-55 release checks remain the real project-specific release
gates after assay 6.3 is integrated.

### RW-255 — 2026-09-16 16:23:27Z — validate staged source-backed P5 dry run

On clean reconciliation tip `6c02e512`, `run-gate.py --base main assay-r1
--dry-run` exited 0 and printed rev 43's source-backed Assay installation
(`pip install --editable <selected-worktree>/assay --no-deps
--no-build-isolation`) with no 6.1.1 artifact or pin. It also exercised the
RG-55 profile/placement planning path without launching a container; observed
host memory PSI `full avg10=0.57%`, below the launch threshold. This does not
replace the tester-unified gate or final mutation evidence.

### RW-256 — 2026-09-16 16:26:25Z — preserve P3 source-reconciliation checkpoint

The isolated P3 close-out draft was appended (not rewritten) with the
2026-09-16 source-backed P5 preparation evidence and committed as `488225be`
on `rg55-closeout-prep`. It records the clean assay source tip, P5
reconciliation commits, the `1184 passed, 3 skipped` local suite, and the
successful rev-43 dry run while explicitly keeping tester-unified, assay 6.3,
mutation, Sol, release, and live-probe evidence pending.

### RW-257 — 2026-09-16 16:27:44Z — correct P3 follow-up scope wording

The P3 close-out draft's opening incorrectly described RG-56 and RG-57 as
outside the wave even though P5 and P4 deliver those follow-ups. It now
distinguishes the RG-55 measurement baseline from the wave's separate
admission/placement and bare-host packages. Historical backlog statuses and
evidence were not changed. The correction is committed as `1a0a936d` on
`rg55-closeout-prep`.

### RW-258 — 2026-09-16 16:30:21Z — authoritative gate observation

After the required observation interval, the P1 fresh R2 supervisor remains
live: Python PID `2389540`, exact container
`run-gate-vbpub-r2-2389540-1789574615`, status `running`, started
`2026-09-16T16:03:35.437556815Z`. The P5 R2 supervisor remains live as Python
PID `2275238` (started by its bare-host lane, so there is no P5 mutation
container to inspect). No progress stream or verdict was read because both
authoritative handles are still live; no mutation result is claimed.

### RW-259 — 2026-09-16 16:41:42Z — remove stale P5 vendored assay artifact

The isolated P5 assay-6.3 reconciliation audit found that the selected
source-backed consumer changes had left the old tracked
`run-gate-project/tools/assay/assay-6.1.1.pyz` and checksum in place. These
two exact files were removed in commit `37fe63aa` on
`rg55-p5-assay63-reconcile`, matching the source-backed consumer contract.
Historical RG-55 reports retain their 6.1.1 evidence references; no active
configuration or mutation lane was changed, and no long-running mutation
state was queried.

### RW-260 — 2026-09-16 16:42:49Z — preserve delivered P5 backlog truth

The operator's assay source-backed branch also changes the run-gate backlog by
reopening the already-delivered P5 RG-56/RG-57 rows and deleting the RG-63
implementation record. Those changes are unrelated to source-backed Assay
consumption and would contradict the P5 implementation checkpoint, so they
were not copied into `rg55-p5-assay63-reconcile`; its P5 FIXED statuses and
RG-63 row remain authoritative pending final gates and release.

### RW-261 — 2026-09-16 16:50:51Z — correct observer deadline

The one-shot observer file was not present at the rough `16:50` label. The
observer itself is still live as PID `2638622`, started at `16:32:39Z`, with a
1210-second sleep; its actual marker target is therefore approximately
`16:52:49Z`. The missing file is an observation timing discrepancy, not a
mutation terminal state; no P1/P5 progress or verdict was read.

### RW-262 — 2026-09-16 16:52:53Z — scheduled observation: both lanes live

The one-shot marker written at `16:52:49Z` confirms both mutation supervisors
remain live. P1 is Python PID `2389540`, with exact container
`run-gate-vbpub-r2-2389540-1789574615` in `running` state; P5 is Python PID
`2275238` with its bare-host `run-gate.py --base main assay-r2` supervisor.
Neither progress stream nor verdict was read because neither handle had
terminated.

### RW-263 — 2026-09-16 16:54:11Z — re-arm observer after launch failure

The first post-observation re-arm left no child or marker and was treated as
a watcher-launch failure. A session-detached replacement is now confirmed:
PID `2755430`, PPID `1`, started `16:54:02Z`, with child `sleep 1210`; it will
write `/tmp/rg55-observe-20260916-1713.log` at approximately `17:14:12Z`.
The P1/P5 mutation handles were not queried during this replacement.

### RW-264 — 2026-09-16 16:55:25Z — assay source branch advances without release

The operator-owned assay source-backed worktree remains clean but has advanced
to `764cb368` (`test(nyxloom): integrate remaining mutation regressions`). No
`assay-v6.3.0` tag is present; its `git describe` output is an unrelated
run-gate tag and is not treated as an Assay release. The P5 final integration
therefore remains pending, and no mutation progress was queried.

### RW-265 — 2026-09-16 16:57:46Z — assay source branch advances again

The operator-owned assay source-backed worktree remains clean and has advanced
to `24ac95df` (`Merge survivor coverage into source-backed Nyxloom branch`).
No `assay-v6.3.0` or other `assay-v6.3.*` tag is present, so the branch is not
yet a releasable Assay 6.3 receipt and P5 integration remains pending. No
mutation progress was queried.

### RW-266 — 2026-09-16 17:05:08Z — source-backed Assay checkpoint and lane preservation

The operator-owned assay source-backed worktree remains clean and has advanced
to `34f63c60` (`test(nyxloom): close follow mutation boundary gaps`), with no
`assay-v6.3.*` tag yet. The P5 assay-6.3 reconciliation worktree remains clean
at `37fe63aa`; its obsolete 6.1.1 artifact has been removed, but final
integration, tester-unified evidence, and the final quiet-tree mutation run
still await the operator's Assay 6.3 release. P1 PID `2389540` and P5 PID
`2275238` remain live, and the exact P1 container remains running. No mutation
progress or verdict was read before the scheduled observation.

### RW-267 — 2026-09-16 17:08:14Z — latest Assay commit does not invalidate P5 selection

The operator's latest source-backed Assay commit `34f63c60` changes only
Nyxloom's session-follow implementation and its tests. A path-scoped diff for
`run-gate-project`, `tester-unified`, `ciu`, `cmru`, and `assay` is empty, so
it does not alter the consumer/configuration surface selected in P5's clean
reconciliation `37fe63aa`. P5 still awaits the actual Assay 6.3 release and
final quiet-tree gates; the mutation supervisors remain untouched and no
mutation progress was queried.

### RW-268 — 2026-09-16 17:10:11Z — Assay release-preparation merge observed

The operator-owned assay source-backed worktree remains clean and has advanced
to `05efe06a` (`Merge current main before Assay source-backed release`). It has
no `assay-v6.3.*` tag yet, so this is preparation rather than a release
receipt. The P5 reconciliation remains clean at `37fe63aa`, and the active P1
and P5 mutation supervisors were not queried before the scheduled observer.

### RW-269 — 2026-09-16 17:15:07Z — scheduled observation confirms live lanes; watcher re-armed

The marker written at `17:14:12Z` confirms P1 PID `2389540` and P5 PID
`2275238` are still live. P1's exact container
`run-gate-vbpub-r2-2389540-1789574615` is `running`; no exit or verdict was
read. The first re-arm attempt exited at the shell boundary without leaving a
watcher, so it was discarded as a watcher failure. A replacement is now
session-detached with watcher PID `3012811`, PPID `1`, child `sleep 1210`, and
marker `/tmp/rg55-observe-20260916-1734.log`; the mutation lanes were not
restarted or otherwise changed.

### RW-270 — 2026-09-16 17:19:53Z — resume P6 after lane-budget exhaustion

P6's terminal verdict for judged tree `8076246c3d365df04ecdd1d2f041ada75c081b40`
is `BUDGET_EXCEEDED/LANE_TIMEOUT`: 484 candidates, with two lane-level
`budget_exceeded` placeholders and no candidate-level timeout. The first
same-tree retry reattached to the stopped container and returned the existing
verdict; that exact old container was already absent when inspected, so no
container was removed by the controller. With memory PSI `full avg10=0.34%`,
one P1 mutation container active, and the detached P6 tree still clean at the
judged commit, the same-tree resume was relaunched successfully as wrapper PID
`3080548`, exact container `run-gate-vbpub-r2-3080550-1789579136`, and capped
at `NanoCpus=3000000000`. A session-detached P6-only observer is verified as
watcher PID `3089418` (PPID 1), targeting
`/tmp/rg55-observe-20260916-1739.log`; no new mutation progress was read.

### RW-271 — 2026-09-16 17:21:44Z — Assay release branch advances without receipt

The operator-owned assay source-backed worktree remains clean and has
advanced to `ea72f244` (`Merge latest main before release gate`). No
`assay-v6.3.*` tag is present, so the branch still has no release receipt and
P5 final integration remains pending. This independent read did not inspect
or alter any mutation stream.

### RW-272 — 2026-09-16 17:27:36Z — CMRU release-safety candidate awaits Assay source integration and Sol

The isolated `cmru-release-dirty-sync` branch is clean at `b1150e6d` and
contains the KI-28 dirty-main cleanup with focused/full local coverage and a
prior Luna `ACCEPT`. Its final review explicitly deferred the registered
Docker gate because the mutation slots were occupied, and the branch's CMRU
gate still references the obsolete 6.1.1 artifact. It is therefore not a
mergeable release candidate while the operator prepares source-backed Assay
6.3; preserve it for reconciliation after that release, then run its real
registered gates and the required fresh Sol review before any merge.

### RW-59 — 2026-09-16 17:33:46Z — controller takeover and review-route constraint

The current Luna xhigh controller has taken over this wave. Existing
checkpoints, detached mutation jobs, and their exact-tree identities remain
authoritative; no mutation stream is restarted or polled ahead of its
scheduled observation. Controller implementation and verification work stays
with Luna xhigh and no new subagents are dispatched. Before every pending
merge, the operator must run the genuinely fresh Sol xhigh final review with
exactly one `REVIEW_TARGET` selected and return its artifact; a Sol review is
not claimed until that artifact is present and ACCEPTs the exact judged tree.

### RW-273 — 2026-09-16 17:37:54Z — correct the takeover ruling number

The log already contained `RW-59` (2026-09-13) when the new takeover entry
was appended. To preserve the append-only record, the immediately preceding
2026-09-16 17:33:46Z entry labeled `RW-59` is hereby identified as the
current-controller takeover ruling for this wave and is to be read as
`RW-273`; no state, constraint, or decision in that entry changes.

### RW-274 — 2026-09-16 17:42:59Z — P6 same-tree resume relaunch

P6's terminal run on judged tree `8076246c` had exactly two lane-level
`budget_exceeded` placeholders and therefore was incomplete. With memory PSI
`full avg10=4.59%`, the detached P6 worktree still clean at that exact tree,
and only P1 occupying a mutation container, the controller relaunched the
same-tree `r2` resume. Wrapper PID `3263920` launched exact container
`run-gate-vbpub-r2-3263927-1789580561`; `docker update --cpus=3` succeeded and
read back `NanoCpus=3000000000`. No commit or tree change was made, and a
session-detached terminal observer was armed for the next 20-minute window.

### RW-275 — 2026-09-16 17:47:27Z — reconcile the CMRU release-safety candidate with main

The isolated `cmru-release-dirty-sync` candidate was clean at `b1150e6d` and
was merged with current `main` using `--no-ff`, producing merge commit
`f4ed5f44`. The merged CMRU configuration now uses the source-backed Assay
consumer path and no longer declares the obsolete 6.1.1 pin; the worktree is
clean. This preparation does not claim CMRU gates, mutation evidence, Sol
acceptance, merge to main, or release; those remain after the operator's
Assay release completes. No running mutation worktree was changed.

### RW-276 — 2026-09-16 17:57:49Z — Assay 6.3 source release and final-tree reconciliation

The operator's source-backed Assay release completed with
`ASSAY_RELEASE_EXIT=0`: its registered self-hosted gate succeeded in 978.3s,
the `assay-v6.3.0` tag and publication completed, and the published wheel
checksum matched `dd80b5fd3287cb3b9ea02a098d26bdea6454b764ab7eaac6ad3a8b5fd5072b6d`.
The wheel is installed in `/home/vscode/.venv` and reports Assay 6.3.0.
The local controller main was merged with the release transaction's remote
main commit as `d0b9d593`; the final P5 reconciliation worktree was merged
with that main as `8a879c52`, with the P5/source-backed documentation and
runner conflicts resolved together. P5's non-mutation `gate-full --base main`
and CMRU's independent assay lane are running detached on quiet trees. P1
and the stale provisional P5 mutation campaign remain live, so no new
mutation lane was launched; P6's same-tree retry still ended with the same
two `budget_exceeded` placeholders and remains incomplete. No Sol acceptance,
merge, or release is claimed by this ruling.

### RW-277 — 2026-09-16 18:15:58Z — restore detached gate observation and advance P3 preparation

The first P5 final-r2 watcher left only an `armed` line and no live process or
terminal result, so it is not evidence. The controller launched the missing
P5 non-mutation `gate-full --base main` from clean reconciled tree `8a879c52`
under the existing niced execution policy (supervisor PID `3693232`) and
armed a proper `nohup` watcher (PID `3693233`) that waits at least one
20-minute boundary before checking the terminal marker. P1 and the stale
provisional P5 mutation supervisors remain live; no mutation tree was touched.

In isolated P3 preparation, the report records the verified source-backed
Assay 6.3.0 publication, installed wheel hash, and release commit as
`984e9823`. This is preparation evidence only: mutation, Sol review, daemon
releases, and live probes remain outstanding.

### RW-278 — 2026-09-16 18:18:11Z — refresh isolated P3 close-out branch from current main

The isolated `rg55-closeout-prep` worktree was clean and had only the P3
report as its intended change relative to its old base, but its raw comparison
to current main appeared broad because main had advanced through the Assay 6.3
source-backed integration. The controller merged current main into that
worktree with `--no-ff`, producing `4e3bb235`; the resulting diff against
current main is only the 152-line close-out report, with the landed SPEC-V8
D.6 note and current main package content preserved. No active gate or judged
tree was changed.

### RW-279 — 2026-09-16 18:23:00Z — terminate obsolete provisional P5 mutation run

The pre-Assay-6.3 P5 mutation process in `rg55-client-v11` was not a valid
release candidate and could not contribute final evidence; its exact wrapper
and child (`2275237`, `2275238`) had remained live and occupied a mutation
slot. After validating both command lines, the controller sent `TERM` to
those two exact processes; both were terminal two seconds later. No Docker
container was removed and no judged tree was modified. P5 final evidence
continues from the clean source-backed tree `8a879c52`; the freed slot is
available for the required P6 same-tree resume once its 20-minute observation
boundary and PSI gate are satisfied.

### RW-280 — 2026-09-16 18:27:04Z — P6 fresh same-tree resume after orphan collection

The first P6 retry only re-collected the already-exited exact container
`run-gate-vbpub-r2-3263927-1789580561` and returned the existing
`BUDGET_EXCEEDED/LANE_TIMEOUT` verdict with two placeholders; it did not
execute new candidates. After that orphan was absent/terminal, the controller
launched a genuine same-tree resume from detached clean tree `8076246c` as
PID `3874052`, creating exact container
`run-gate-vbpub-r2-3874052-1789583180`. The container is in
`dev-background.slice` with `NanoCpus=3000000000`; memory PSI was
`full avg10=0.23` at launch. A one-shot 20-minute observer PID `3880578` is
armed. No commit or judged-tree mutation was made.

### RW-281 — 2026-09-16 18:30:55Z — record cockpit carrier limitation for P3

The read-only cockpit audit found no `/run/cgprofile` directory or
`/run/cgprofile/ctl.sock`; systemd is not running inside this devcontainer,
`CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice`, and
`CGROUP_PARENT_DEV_GATES` is unset. The controller therefore claims no
socket-carrier or gates-slice success. The isolated P3 report records that
the eventual live probes must use the approved docker-exec carrier and must
disclose any observed `place-refused:no-gates-slice` result until the
devcontainer is rebuilt with the host mount.

### RW-282 — 2026-09-16 18:36:22Z — relaunch P5 gate-full with captured child status

At the scheduled P5 observation, the earlier gate-full attempt had ended with
only partial selftest output and no authoritative child exit marker. Its
watcher-derived `EXIT=1` was therefore discarded as inconclusive rather than
treated as a product failure. From clean reconciled tree `8a879c52`, the
controller relaunched `./run-gate.py --base main gate-full` using a direct
detached wrapper that captures the child status as
`P5_GATE_FULL_EXIT=<rc>`; wrapper PID `3982944` was live after launch and
reported the expected rev-43 selftest start. The prior P5 final R2 was never
launched. P1 and P6 mutation runs remain untouched.

### RW-283 — 2026-09-16 18:38:35Z — audit RG-55 follow-up backlog scope

The current run-gate backlog audit finds RG-55 and the wave's RG-57 through
RG-61 rows marked FIXED, with RG-56 explicitly OPEN as the subsequent
admission-control wave. RG-45, RG-49, and RG-54 are also OPEN, but each
predates this wave and is an independent issue (Assay/Vitest contention,
state-dir placement, and merge-base selection respectively); none was
introduced by the current RG-55 work. They remain filed for their own scope
and are not silently folded into this release. No active gate was queried or
changed by this audit.

### RW-284 — 2026-09-16 23:21:15Z — repair RG-49 provider blocker B9

The received final review artifact
`run-gate-RG49-SOL-FINAL-REVIEW.md` is preserved as **REJECT**: B9 remains a
release blocker because Assay crashes on a JSON `null` mutation resume record
before writing a verdict. Its linked
`.run-gate/rg49-review-evidence/postrecord-gates.json` is preserved unchanged
and remains bound to candidate HEAD
`32a8f97c8ef6fe0c602288e112f2e77ece779d1e`.

The narrow provider repair is implemented in isolated worktree
`.worktrees/rg49-assay-b9`, commit
`12e061b1856e919e1dc3bedb36bf808cecfabec`, adding object-root validation via
the existing `MutationStateError` structured `ERROR` /
`UNREADABLE_ARTIFACT` path, with synchronized adopter documentation and a
backlog row. The focused identity/record suite passes 57 tests and all
mutation-focused tests pass 267 tests. The whole Assay suite has one unrelated
pre-existing dstdns witness-qualification failure (4,750 passed, 20 skipped);
no dstdns path was touched. No merge, release, or RG-49 gate evidence is
claimed for this repair. A genuinely fresh, route-verified Sol xhigh review is
required before merging or releasing it; the supplied review disclosed that
its Sol route metadata was unavailable.

### RW-285 — 2026-09-17 22:22:14Z — defer Assay 6.3.2 launch at PSI gate

The accepted fresh Sol xhigh provider review is recorded at
`assay/nyxloom-trove/reports/.run-gate/run-gate-RG49-B9-SOL-FINAL-REVIEW.md`
on final HEAD `e6ac473082aab158f598772a6039f11a6f2d5f39`; its B9, B11 and B12
repairs were merged into main as `fb24a852`. The installed cockpit still
reports Assay 6.3.0; `cmru status --project assay --set-version 6.3.2`
selects 6.3.2 because 6.3.1 is already tagged.

The first detached release wrapper produced no child-exit marker and no
release worktree, so it is not evidence of success or failure and is not
counted. A retry was refused before launch because host memory PSI was
`full avg10=5.42`, above the mandatory `<=5` threshold. No release, gate, or
container was launched under that pressure. The next controller must perform
one fresh PSI check at a meaningful wake point, then launch CMRU with a
detached child-exit marker only if the gate is satisfied.

### RW-286 — 2026-09-18 09:37:01Z — launch final P1 R2 on current provider tree

The installed release state now reports Assay 6.4.0 from the controller venv
and run-gate rev 43; run-gate 23.9.0 is tagged. The P5 implementation has no
post-release code/test diff, only RG-63/RG-65 backlog documentation, so its
existing release evidence is retained without a redundant review.

The clean P1 worktree `rg55-profiler-daemon` was reconciled with current main
and current source-backed Assay 6.4.0 in merge commit `fc5d2737`. Its final
judged HEAD is `fc5d27370a848a24f9a62f69ba1a031bf0a3aac6`. With host memory
PSI `full avg10=2.24`, the controller launched the required final `r2` lane
detached from `scripts/cgroup-profiler/`; wrapper PID `3442381` writes its
authoritative child marker to `/tmp/rg55-p1-r2-final-20260918.log`. HEAD is
held quiet and no progress polling is authorized before a meaningful
completion boundary.

### RW-287 — 2026-09-18 10:34:51Z — correct P1 merge-base and defer launch on PSI

The first final P1 R2 attempt on merge HEAD
`fc5d27370a848a24f9a62f69ba1a031bf0a3aac6` exited 5 with a separately read
`INCONCLUSIVE/NO_MUTANTS` verdict. Assay resolved the merge commit's first
parent `4845a58a` as its base, leaving no changed `lib/` lines; this is a
base-shape artifact, not mutation evidence and not a product verdict.

To remove that ambiguity, the controller created linear worktree
`rg55-profiler-daemon-final` from current main and imported only the P1
`scripts/cgroup-profiler/` snapshot. Its clean final HEAD is
`9b70a46e902b5ea63ea9de699165593ddae8bd54`, with current source-backed Assay
6.4 and a real `origin/main..HEAD` package diff. The corrected R2 launch was
then refused because memory PSI was `full avg10=8.72`, above the mandatory
`<=5` gate. A one-shot 20-minute detached watcher PID `3525316` is armed;
it launches only after a single wake-point PSI check at or below 5, otherwise
it records `P1_R2_CORRECTED_PSI_BLOCKED=1`. No mutation container was started
under the red gate.

### RW-288 — 2026-09-18 10:35:53Z — prepare linear P6 final tree

While P1 waits for the PSI wake point, the controller materialized
`rg55-followups-cgprofile-final` from current main and imported the exact P6
package snapshot from judged tree `8076246c`. The resulting linear final tree
is clean at `c362c8dffb41cad6c7c4c57fc69e5163eacd8f3b`, with current
source-backed Assay 6.4 and no mutation/container launch. P6 remains queued
behind a green PSI check and the estate's two-slot mutation limit.

### RW-289 — 2026-09-23 08:05:14Z — adopt B101-era main and launch fresh P1 short gate

The controller has resumed the RG-55 wave on current `main` `7a3c0571`, after
the assay B101 P1 merge and handoff. The cockpit was missing the Assay console
and still had an older run-gate development install; both were rebuilt from
this clean checkout into `/home/vscode/.venv`, yielding Assay
`6.5.1.dev237+g7a3c0571` and run-gate `23.9.2.dev305+g7a3c0571` (rev 46).
These are development identities, not stable release claims.

P1 was reconciled into a new linear worktree
`.worktrees/rg55-p1-current`, preserving current main's estate-CLI
compatibility and CP-4 profiler backlog additions. The reconciliation commit
is `ced40659`; the tree is held quiet for judging. With memory PSI
`full avg10=0`, the controller launched only the short `r0-r1` gate detached
from `scripts/cgroup-profiler/`, with wrapper PID `322897` and authoritative
marker `/tmp/rg55-p1-r01-20260923.log`. No other RG-55 gate was active at
launch. P6 remains queued until the assay B101 P2 shallow-seed announcement;
its old judged tree is not reused.

### RW-290 — 2026-09-23 08:08:50Z — preserve RG-55 backlog identity during P1 reconcile

Current `main` contains a post-wave profiler row named CP-4 for the
`cmd_targets` duplicate-mount defect. The P1/P6 RG-55 records already use CP-4
for the pre-existing `test_store.py` run-id flake, and the P6 handoff reserves
the RG-55 follow-up namespace through CP-11. The reconciliation therefore
preserves the historical RG-55 CP-4..CP-11 identities and renumbers only the
post-wave `cmd_targets` row to the next available CP-12 identity, updating its
filename, frontmatter, and generated index. This is an identity collision
repair, not a product-scope change or a claim that CP-12 is fixed.

### RW-291 — 2026-09-23 08:23:42Z — discard lost P1 short-gate launch and rearm

The first detached P1 `r0-r1` wrapper (PID `322897`) disappeared with empty
stdout and no child-exit marker; no gate container was created, so it is
inconclusive and contributes no evidence. The one-shot continuation was
cancelled before it could launch mutation. The reconciled P1 tree then fixed
the duplicate backlog identity by renaming the post-wave `cmd_targets` entry
to CP-12 in commit `32ec2b3d`; the historical RG-55 CP-4 flake remains CP-4.

After a fresh PSI check (`full avg10=0`) and an empty gate slot, the controller
re-launched `r0-r1` from that quiet tree with disowned wrapper PID `354688`,
authoritative log `/tmp/rg55-p1-r01-20260923.log`, and a single disowned
20-minute continuation PID `355510`. No mutation verdict is claimed yet.

### RW-292 — 2026-09-23 08:27:42Z — replace reaped background wrappers with PTY carrier

The supposedly disowned replacement wrappers were independently verified as
reaped immediately: their logs remained zero bytes, no child exit markers were
written, and no RG-55 container existed. Those launches are therefore
inconclusive and are not gate failures. The host preflight again found memory
PSI `full avg10=0` and an empty gate slot. The controller started the fresh P1
`r0-r1` command in tool-managed PTY session `77256` from quiet tree
`32ec2b3d`; the session printed rev 46, the selected lane, its 20-minute
budget, and PSI `full avg10=0.14%`. Its child marker will be written to
`/tmp/rg55-p1-r01-pty-20260923.log` on completion. No mutation launch is
authorized until that marker is read separately and reports PASS.

### RW-293 — 2026-09-23 08:50:27Z — repair P1 short-gate coverage oracle

The persistent PTY P1 `r0-r1` run completed with 1,206 tests passed but exit
2 because total coverage was 99%: `cgprofile.py:70`, the current-main
`CgprofileArgumentParser.format_usage()` compatibility line, was unexercised.
This is an oracle gap in a changed line, not a product or infrastructure
failure. The controller added the direct usage-headline regression
`test_usage_starts_with_the_headline` in `a2c2501f` and started a fresh quiet
rerun in PTY session `24764`, with PSI `full avg10=0.83%`. Its marker is
`/tmp/rg55-p1-r01-rerun-pty-20260923.log`; mutation remains blocked pending a
PASS.

### RW-294 — 2026-09-23 09:06:32Z — P1 short gates green; mutation campaign healthy

The repaired P1 tree passed `r0-r1` in 64.90 seconds with 1,207 tests and
100% line and branch coverage. The required `r3` canary lane then passed with
all seven canaries rejected and exit 0. Both lanes used the expected coarse
profiling fallback because `cgprofile-host-daemon` is not running; no gate
verdict was affected.

After a fresh PSI `full avg10=0` check and an empty gate slot, the controller
started P1 `r2` in PTY session `45281` from quiet HEAD `a2c2501f`; the lane
declared its 4-hour advisory budget and created container
`run-gate-vbpub-r2-522284-1790154369`. The 10-second health check showed the
container active, host PSI `full avg10=2.68%`, and Assay entering its
source-backed judge. The missing installed-distribution provenance notice is
expected for this in-repo source-backed consumer. Prior P1 history estimates
about 5–6 hours; no progress polling is due before the 25-minute boundary.

### RW-295 — 2026-09-23 13:36:04Z — P1 R2 complete; one equivalent survivor

The persistent P1 R2 carrier session `45281` completed and was read from its
authoritative verdict, not from the wrapper status. Its exact tester container
was `run-gate-vbpub-r2-522284-1790154369`; it has exited and no RG-55 gate
process remains. On quiet HEAD `a2c2501fea2b576a774f1a7fcedd3a5ad251f630`,
Assay accounted for 250 candidates: 249 killed, 1 survived, 0 equivalent,
0 budget-exceeded, 0 crashed, 0 hung; exit 1 `MUTANTS_SURVIVED`. The sole
survivor is `lib/damon.py:325`, `Gt->GtE` on the pool release guard. The
existing P1 report's invariant proof applies: after a live owned slot exists,
`current == expected_end` implies `current > baseline`, so the replacement
cannot change a reachable outcome. It is an assay-classification limitation,
not an untriaged oracle gap. The result remains mutation evidence, not a green
R2 verdict; any provisional integration must retain this disclosure and the
verdict/progress artifacts.

### RW-296 — 2026-09-23 13:36:04Z — operator changes long-gate integration workflow

The operator authorizes provisional package integration once full line/branch
coverage, the short rigor gates, and the required adversarial review report no
new issue, while a long R2 mutation run continues in an isolated `ciu
worktree`. This provisional integration is not a release or a DONE claim.
Mutation evidence remains authoritative per exact tree; a survivor that is not
proven equivalent, or any discovered defect, must be fixed/backported and
invalidates the affected provisional evidence and gates. The final merge and
release still require the fresh Sol xhigh review requested by the operator,
the survivor disposition recorded in the package report, and a final quiet
gate set; no mutation result may be silently waived.

### RW-297 — 2026-09-23 13:38:30Z — queue P1 R2 in a CIU-managed worktree

The controller created the managed checkout
`.worktrees/rg55-p1-r2-ciu` (`ciu worktree create rg55-p1-r2-ciu`) at exact
judged tree `a2c2501f`, branch `rg55-p1-r2-ciu`, for the parallel long-run
workflow. Launch is deferred because the estate's single gate slot is occupied
by the unrelated Assay B101 gate container
`run-gate-assay-selfhosted-687027-20849-1790170568`, whose exact command and
process are `assay-b101-gate-clean` / PID `686965`. The controller will not
stop or contend with that run. The P1 CIU worktree is clean and ready; no P1
R2 container is currently running.

### RW-298 — 2026-09-23 13:40:33Z — preserve R2 disposition in queued tree

The P1 report now carries the current R2 evidence and the explicit
`damon.py:325` equivalence disposition in commit `9471a9af`. That report-only
commit was mirrored into the managed CIU checkout as `07161416`; the queued
checkout remains clean and is the tree to judge when the single gate slot is
free. The original R2 ran from `09:06:11Z` to `11:48:41Z` (2h42m30s), so the
new run is expected to be a multi-hour task; it will receive the normal
10-second health check and then no progress read more often than every 25
minutes.

### RW-299 — 2026-09-23 13:59:17Z — controller review round 2 started and recorded

The controller performed a fresh read-only adversarial pass on P1's current
integration tip `9471a9af` (source tree unchanged from the short-gated
`a2c2501f`; the intervening commit records the R2 disposition). The review
covered daemon write safety and lifecycle, contract/CLI validation, incremental
summary arithmetic, subtree discovery, DAMON ownership, recovery/retention,
image and ciu configuration, documentation, and the repaired round-1 B1–B8
blockers. No new merge-blocking issue was found. The review record is
`cgprofile-P1-DAEMON-REVIEW-round2.md`, commit `3455999a`, and is
`ACCEPT-CONDITIONAL`: the one reachable-state-equivalent survivor remains
disclosed, final quiet gates remain required, and the standing independent
release review requirement is not waived.

### RW-300 — 2026-09-23 14:00:02Z — provisionally integrate P1 while R2 runs asynchronously

The operator's revised workflow is applied. P1 has 100% changed line and
branch coverage, green short gates (`r0-r1` and `r3`), and the controller's
round-2 review found no new blocker. The P1 implementation may therefore be
merged provisionally while the long R2 campaign continues in the separate
CIU-managed worktree `rg55-p1-r2-ciu` at its quiet judged tree. This does not
claim R2 PASS, release, or wave completion. A non-equivalent survivor,
regression, or review finding requires a backport and invalidates affected
evidence; final quiet gates and the required release review remain mandatory.

### RW-301 — 2026-09-23 14:22:05Z — P6 reconciled with current P1 and locally green

The controller reconciled the P6 worktree `.worktrees/rg55-followups-cgprofile-final`
with the current P1 implementation. The merge preserved P6's CP-12 ownership;
the unrelated current-main backlog item was renumbered CP-13. During
reconciliation, the initial synchronous sample was wired into the liveness
tracker so the summary and liveness views share the same baseline. The P6
focused daemon suite then passed 591 tests with 6 skips. The four root frozen
contract fixtures were updated to the P6 package version 1.1.0 and remain
byte-identical to the package copies. Reconciliation commit: `bb1042a6`.
This is a source/test checkpoint only; registered gates, adversarial review,
mutation disposition, merge, and release remain outstanding.

### RW-302 — 2026-09-23 14:26:03Z — P6 controller review conditional; gate slot occupied

The controller reviewed P6 at `13e394a0` after the P1 reconciliation and
found no new merge-blocking defect. The review is explicitly conditional: it
does not replace the required fresh Sol xhigh review, current-tip mutation
evidence, fresh registered gates, or current live probes. The review also
fixed and recorded the adopter-facing 1.1.0 examples in README,
CONSUMERS.md, and DESIGN-GUIDE.md (`54e0a364`); the interface contract mirror
remains byte-identical.

At the first permitted 25-minute state check, P1's isolated mutation container
`run-gate-vbpub-r2-765950-1790171671` was still up with its exact CIU runner
(`765950`). Two unrelated Assay B101 gate containers were also active. Memory
PSI was over the launch threshold (`full avg10=5.08`), so no P6 gate or probe
was launched and no running job was disturbed. P6 remains queued for the next
quiet, PSI-admitted slot.

### RW-303 — 2026-09-23 14:33:20Z — retain the quiet-slot decision after takeover review

The controller's next state check found P1's asynchronous R2 still active in
`rg55-p1-r2-ciu` (`run-gate-vbpub-r2-765950-1790171671`, runner PID 765950)
and an unrelated Assay B101 gate container still running. The memory PSI
launch signal had fallen below the threshold (`full avg10=0.89`), but the
estate's current one-gate-container rule still leaves no admitted slot for a
P6 short gate. No running job was stopped, and P6 remains queued at its quiet
reviewed tip `8e52ea08`.

### RW-304 — 2026-09-23 14:36:08Z — reconcile P6 with the current source-backed Assay

Before launching P6's mutation campaign, the controller compared its exact
judged checkout with current `main`. P6 tip `8e52ea08` does not contain the
already-merged Assay B101 shallow-snapshot seed `5bf832a4` (nor the subsequent
current-main Assay source), while P6's `run-gate.toml` deliberately consumes
the checkout's own `assay/src` at lane-run time. The P1 campaign is already
running on its quiet judged tree and is not changed. P6 will be reconciled
onto current `main`, revalidated, and only then receive its exact-tree short
gates and R2 launch; the pre-reconciliation CIU checkout remains untouched
until the new tip is settled.

### RW-305 — 2026-09-23 14:39:49Z — P6 current-main reconciliation reviewed

P6 was reconciled with current `main` in private merge commit
`cc9d13b6`, retaining the P6 cgprofile implementation while adopting the
current source-backed Assay tree and its B101 shallow snapshot seed. The
reviewed P6 CIU checkout is now exact tree `4392bece` after the report-only
controller review round 2; it is clean and no mutation has started. The
focused local suite passed `445 passed, 6 skipped`; the full tester-unified
gate remains authoritative because the cockpit lacks NumPy/report-tier
dependencies. The short registered gates remain deferred until the already
active P1 mutation and unrelated B101 gate release the estate's one gate
slot.

### RW-306 — 2026-09-23 18:58:14Z — P1 R2 terminal and P6 `r0-r1` green

The P1 CIU mutation run finished on quiet tree
`071614168b2a96cab0b90b1f0d4c439972bf54dc`. The terminal side-file event
records 250 candidates: 249 killed, one survivor, and zero equivalent,
budget-exceeded, crashed, or hung outcomes. The separate verdict records R0
PASS and R2 `FAIL/MUTANTS_SURVIVED`, exit 1. Its sole survivor is the already
documented equivalent `lib/damon.py:325 Gt->GtE` on the pool release guard,
with the same replacement hash and invariant proof. The CIU tree's package
source is unchanged from the reviewed and short-gated P1 source; its exact
result is in the P1 report.

P6's registered `r0-r1` lane passed on quiet tree
`4392bece345d828e88f25f2b13cd7b239f7d711a`: 1,474 tests passed, and coverage
reported 5,560/5,560 statements and 1,862/1,862 branches. Run-gate history
records `exit_code=0`, `dirty=false`, and the same tree. Tester container
`upbeat_elbakyan` ran under `dev-gates.slice` at 3 CPUs; it exited and was
removed by the lane. Host memory PSI `full avg10=0.00`; the slot was free at
the follow-up check. P6 `r3` is next; no mutation or merge has started.

### RW-307 — 2026-09-23 19:08:43Z — P6 `r3` green; version identity needs resolution

P6's registered `r3` canary lane passed on quiet tree
`4392bece345d828e88f25f2b13cd7b239f7d711a`: all 7 canaries were rejected,
zero survived, and run-gate reported exit 0. The final detached gate
container was `run-gate-vbpub-r3-1174471-1790190183`, under
`dev-gates.slice`; its 10-second health receipt records `cpus=3000000000`
while running. The container and runner had exited by the subsequent check;
memory PSI `full avg10=0.00` and the slot is free. The earlier `r0-r1` and
`r3` receipts are for this exact pre-reconciliation P6 tree, not evidence for
any later tree.

The controller review found that CMRU's OCI release coordinate (planned
1.1.0) is injected into image tags/labels, while `lib/serve.py` still
hardcodes `CGPROFILE_VERSION = "1.0.0"` and the CLI reports the separate
`pyproject.toml` version `0.1.0`. Before P6 release, resolve whether this
separation is intentional under the frozen contract; if not, propagate the
release coordinate to the running daemon's self-description and test it.

### RW-308 — 2026-09-23 19:44:04Z — enforce the no-host-namespace constraint

The operator's current repo-wide AGENTS.md instruction and the RG-55
controller constraints prohibit `--cgroupns=host`, `--pid=host`, and
`--net=host` for any spawned container. The P1 compose template currently
sets host cgroup and PID namespaces, which conflicts with that binding rule.
Do not deploy that template as-is. Preserve the daemon's required host
observability through explicit, read-only host `/proc` and cgroup-v2 bind
mounts while leaving both namespaces private; keep DAMON's narrowly scoped
write surface separate. Prove the actual host paths and daemon probes live
before deployment. This is an implementation reconciliation of the existing
host-observation contract, not permission to use host namespaces or to weaken
D-15. The gate launcher was independently amended at `3ce08349` to use the
read-only host cgroup bind mount, immediately cap its named containers at 3
CPUs, and assert both the cap and cgroup parent.

### RW-309 — 2026-09-23 20:43:17Z — resolve proc cgroup paths relative to the private namespace

A live P1-shaped probe used private PID/cgroup namespaces plus read-only host
`/proc` and cgroup-v2 binds. It confirmed that `/hostproc/1` is the host init
and that PID 1's PID-namespace inode differs from the container's; it also
showed `/hostproc/1/cgroup` as `/../../../init.scope` while the helper's own
local path is `/`. A bind-mounted host procfs does not make
`/proc/<pid>/cgroup` globally rooted: those paths remain relative to the
reader's cgroup namespace. Therefore P1 must derive that namespace root from
the helper's own visible PID membership in the mounted cgroup tree and local
`/proc/self/cgroup`, then normalize each observed process path against that
derived root. Reject missing/ambiguous membership or an absent resolved
cgroup; do not use a host cgroup namespace to avoid the translation.

### RW-312 — 2026-09-24 00:49:07Z — scope token roots to the selected cgroup

The private-PID fix must preserve §2.2's attribution boundary: resolve the
positive PIDs directly in the selected target cgroup (using the explicit host
proc view when `cgroup.procs` exposes only zero), then match the exact token
among those PIDs. Do not search all host processes for token roots. Their
descendants remain attributed if they later move to another cgroup. A target
of `/` means processes directly in the hierarchy root, not every descendant.
This also avoids resolving the no-token PID list a second time on token-backed
session startup. P1 source and regression tests now encode this ruling; fresh
registered gates are still required after the edits.

### RW-313 — 2026-09-24 00:49:07Z — distinct gate worktrees may run concurrently

The operator clarified that independent CIU worktrees may host concurrent gate
containers; the other controller's local one-at-a-time scheduling choice is
not a host-wide exclusion rule. Each container still needs its own unique
exact name, read-only source bind, verified loaded `dev-gates.slice` parent,
and immediate 3-CPU cap. Worktree isolation separates source/evidence, not host
resources; keep the two-mutation-lane ceiling and the memory-PSI admission
guard (`full avg10` must be at most 5 before launching work). The Wave C
`run-gate-assay-selfhosted-1477047-21288-1790210014` and P1
`cgprofile-gate-1478779-1790210092` containers did overlap; P1's exact-tree
`r0-r1` completed cleanly with 1,324 tests and 100% statement/branch coverage
in 72.891 seconds. The P1 tree changed afterward under RW-312, so this result
is historical, not final evidence. Earlier this session I also mistakenly
launched a local targeted pytest when memory PSI full avg10 was 6.97; collection
stopped because the devcontainer lacked `numpy`, and no test body ran. I will
not launch further validation until the admission threshold is met.

The P1-local RW-310/RW-311 were renumbered RW-312/RW-313 because canonical
main already assigns RW-310/RW-311 to cockpit/BuildKit and release-identity
rulings. The P1 meanings are preserved above.

### RW-310 — 2026-09-24 06:04:07Z — record current cockpit source and BuildKit socket remediation

The operator confirms the running devcontainer is based on
`/workspaces/dstdns/.devcontainer/devcontainer.json`. This is context only:
the controller must not inspect or modify `/workspaces/dstdns`; all durable
host-setup changes belong upstream in
`modern-debian-tools-python-debug/host-setup`.

The rootless BuildKit socket was inaccessible because it was created as
`1000:1000` mode `0660`, while the cockpit has the Docker group GID but not
GID 1000. The live host was corrected to use a sticky shared runtime directory
and a post-start socket `chgrp docker`/`chmod 0660`; no service restart was
performed. Matching upstream source is committed on
`rg55-buildkit-socket-gid` at `7f0f46f3`; its registered `smoke` lane passed
(`85 passed, 6 skipped`, exit 0). This branch remains unmerged and requires
independent review; the smoke result is not release evidence. No file under
`/workspaces/dstdns` was read or changed.

### RW-311 — 2026-09-24 06:14:06Z — bind daemon version identity to CMRU's release tag

RW-307's version-identity question is resolved: CMRU is the authoritative
source of the OCI release coordinate, so the built CLI and daemon self-report
must both use that same coordinate. In the P6 worktree, commit `dfef6bad`
reads the `cgprofile-v<version>` tag at `HEAD`, validates it, passes the exact
value through the build to `CGPROFILE_VERSION`, and uses that embedded value
for both CLI and daemon identity. Untagged local builds identify as
`0.0.0-dev`; an explicitly present but empty/malformed version now refuses
rather than silently falling back. The README, DESIGN-GUIDE, and CONSUMERS
examples were updated with the behavior and release flow.

The controller verified CMRU's order locally: project gates run before tag
creation, then the release tag is created/pushed before build and publish. A
read-only CMRU status against the P6 worktree reports no existing cgprofile
tag, so P1's first release must keep the settled explicit `--set-version
1.0.0` rather than accepting the SCM first-release default `0.1.0`. Focused
version/build/CLI tests pass (`26 passed`); registered P6 `r0-r1` is running
on the clean `dfef6bad` tree, and `r3`, P6 R2, independent review, and release
remain outstanding.
### RW-314 — 2026-09-24 06:41:22Z — make P6 placement's host cgroup write view explicit

RW-308's prohibition on host namespaces remains absolute. Its read-only host
cgroup bind is superseded for the P6 daemon because D-25/RW-35(a) already
requires opt-in placement to create a leaf, delegate controllers, move lane
PIDs, and restore survivors to their origin cgroup. The daemon therefore gets
an explicit writable host cgroup-v2 bind; `CgroupWriteGuard` is the
program-level allowlist and every admitted write is recorded. Host `/proc`
remains read-only, `--network none` remains set, and namespaces stay private.
The one-shot helper keeps read-only host `/proc` and cgroup mounts. Compose,
user docs, both byte-identical contract copies, review handoff, and a
deployment-structure test now encode this distinction. This implements the
settled placement contract without reopening the namespace ruling.

### RW-315 — 2026-09-24 06:41:55Z — disposition of the first P6 reconciled-tree gate

P6's registered `r0-r1` run on `dfef6bad8b0cb8cc97d87c27b399e4a101ed2d8b`
started at 06:12:01Z, ended at 06:14:16Z, and failed with exit 1 after
1,494 passed and one failed. The failure was
`TestPeerCredentials.test_a_real_socket_peer_is_this_process_uid`, which
raised `BrokenPipeError` on its second socket request. The gate also logged a
best-effort socket `chown` permission warning; causality is not established.
I did not rerun that old tree. Its `tools/gate.sh` placement probe used
`docker run --rm --cgroupns=host`, violating RW-308; that exact container
auto-removed, and the reconciled script no longer contains a host-namespace
flag. After P1/P6 source reconciliation and test corrections, the socket test
file passed (40 tests) and the targeted deployment/version/build/CLI/access
set passed (178 tests) locally under the PSI gate. Those are diagnostic
results only; registered `r0-r1` and `r3` must pass on a quiet committed tree.

### RW-316 — 2026-09-24 07:00:46Z — disposition of the reconciled P6 R0/R1 run

The registered `r0-r1` run on `dce2b91a061c4d0cbb2f1d102ac87582cc7c9122`
started at 06:48:35Z and ended at 06:50:44Z (129.062 s), exit 1. It reported
1,589 passed and three failed. Two CLI serve tests had stale test seams after
private-host-proc preflight was added: they stubbed the host-cgroup check but
not `have_host_proc_view`. The placed-start live-document test now supplies a
token-bearing fake `/proc/101/environ`; its actual `pids_moved: 1` correctly
differs from the old golden's `pids_moved: 0`. The test harness repairs are
uncommitted at this ruling; the golden will be updated to the demonstrated
behavior. This is a test-oracle/harness correction, not a production-code
change. Focused verification and fresh registered gates remain required.

### RW-317 — 2026-09-24 07:05:25Z — close the P6 gate-harness mismatch with behavioral proof

The token-owning fake process made the placed-start oracle exercise the
intended behavior: `pids_at_start` and `pids_moved` both report one. The
`start-placed-v1.1.json` golden is now updated from zero to one, and the two
CLI tests stub the newly required host-proc preflight. The corrected focused
set (`tests/test_serve.py`, `tests/test_serve_placement.py`,
`tests/test_deployment_contract.py`) passed 247 tests with 6 skipped in
25.39 s; the individual golden/deployment subset passed 3 tests. README,
DESIGN-GUIDE, the P6 Sol handoff, and this global review packet were reconciled
to the private-namespace / writable-guarded-daemon-cgroup design. These edits
are still uncommitted and do not validate the registered gate; run fresh
`r0-r1` and `r3` on the exact committed tree.
### RW-318 — 2026-09-24 07:26:57Z — disqualify the legacy P1 gate receipt

The nested `r0-r1` invocation on stale candidate
`9b70a46e902b5ea63ea9de699165593ddae8bd54` reported exit 0, 1,202 tests
passed and 100% line/branch coverage (history duration 69.198 s). The exact
test container `bold_dewdney` was in `dev-background.slice`, not the required
loaded `dev-gates.slice`; inspection showed the gate's placement probe used
`--cgroupns=host`. I applied `docker update --cpus=3` and verified
`NanoCpus=3000000000`, then stopped that exact container. This receipt is
therefore NOT accepted as RG-55 shipping evidence despite the test/coverage
PASS. The P1-private-ns branch carries the corrected named-container launcher
and private-namespace probe; its reconciled exact-tree gate is still required.
No unrelated container was stopped.

### RW-319 — 2026-09-24 07:41:38Z — permit a third mutation lane under verified gate capacity

The operator supersedes the earlier hard two-lane ceiling. The host's
`dev-gates.slice` is verified loaded with `CPUQuotaPerSecUSec=5s` (five CPU
equivalents); allow up to **three** concurrent mutation containers estate-wide.
This is permission to use the bounded capacity, not a claim that three
three-CPU containers can receive five CPUs simultaneously: CPU scheduling and
contention are expected, allowed conditions. Each lane gets a unique exact
container name, the declared and verified `dev-gates.slice` parent, and an
immediate verified `docker update --cpus=3`. Check memory PSI before launch and
do not launch while `full avg10 > 5`.

Functional outcomes and mutation classifications must be deterministic and
agnostic to scheduler contention. Time/budget controls exist only for safety
and resumability; timeout, interruption, pressure, or an unjudged candidate is
incomplete/infrastructure evidence, never a product verdict. Do not change
test semantics or classify a candidate from elapsed wall time. The controller
must update review/dispatch packets that still encode the former two-lane cap.

### RW-320 — 2026-09-25 02:49:51Z — P1 R2 survivors require behavioral oracles

The exact registered P1 R2 on `51198f2e4759acbd69dfd770b843cdf20b1d6ed0`
finished at `2026-09-24T09:19:52.297677Z`: 81/81 candidates executed, 71
killed, 10 survived, and zero equivalent, budget-exceeded, crashed, or hung.
The ten survivors at `access.py:390`, `targets.py:262-267`, and
`version.py:52,57,60` are behavioral-oracle gaps, not accepted equivalents.
The P1 worktree now contains focused tests for diagnostic flush-before-start,
nonpositive-PID refusal, both missing namespace mapping facts, malformed
prefix refusal, subprocess option semantics, and both diagnostic fallback
paths. The focused 235-test suite passed. These edits plus this ruling/report
change the tree, so the `51198f2e` mutation records are not release evidence
for the next candidate. Commit all repair and record changes before starting
the next registered R2; then keep that exact tree quiet through R2 and final
short gates. The mechanical outcome for `51198f2e` remains
`FAIL/MUTANTS_SURVIVED`.

### RW-321 — 2026-09-25 03:06:05Z — reconcile P1 to current main before long mutation

Before launching its next long R2, the P1 worktree was found behind `main`
(`b52dc9f8` versus `7f465669`). The intervening main diff was read and
contained only the 61-line assay backlog entry in
`assay/nyxloom-trove/4-backlog.md`; no P1 implementation code changed.
Current main was merged cleanly into the P1 branch as `a3b4dc1c`, ensuring
the source-backed gate picks up main's current assay tree. All short-gate and
mutation evidence must now match the post-reconciliation commit; rerun both
short gates before starting the new R2. This reconciles an already-settled
candidate with main and does not reopen the P1 design decisions.

### RW-322 — 2026-09-25 10:30:44Z — P1 R2 terminal is incomplete; reconcile before new evidence

The detached registered P1 R2 on quiet tree
`1908316b227df8a2b8fd259969725b4c7a9f1b27` ran in
`run-gate-vbpub-r2-3690082-1790305991` from
`2026-09-25T03:13:17.949231Z` to `2026-09-25T04:27:11.853270Z`. All 81
candidates are accounted for: 80 killed, one `hung`, and zero survivors,
equivalents, per-candidate budget excesses, or crashes. The separate verdict
is `BUDGET_EXCEEDED/CANDIDATE_HUNG`, exit 4; do not call it a PASS or a
mutant survival. Candidate 40 (`7692929c11d002ae02db963540fb42e06618318c814e1e99e71fed42894eea9c`,
`lib/targets.py:191`, `Eq->NotEq`) reported 1,321 completed tests but has no
kill proof in the retained candidate-state/progress artifacts. RW-319 binds:
do not explain this as scheduler contention or let elapsed time decide
product behavior. Diagnose it from a fresh, exact-tree run.

The campaign's judged tree predates current-main Assay Wave C P2/P3. P1 has
now reconciled current `main` as `007b208859b99d380b87a9d4ea3479bdbfc8d5b6`;
the old mutation record is inapplicable. Fresh P1 `r0-r1`/`r3` and the
required fresh Sol xhigh review are next. Under the operator's provisional
merge workflow, after those short gates and review pass, merge P1 to unblock
P6, then launch the new R2 and full gate in an isolated CIU worktree. Any
repair is backported and its affected evidence rerun.

### RW-323 — 2026-09-25 10:43:38Z — require P1 container caps before execution

P1's fresh R3 on `d94c58b9` rejected all seven canaries, but live inspection
of its exact container `run-gate-vbpub-r3-4169091-1790332576` showed
`NanoCpus=0`. The attempted exact-name `docker update --cpus=3` found that
the short-lived container had already exited. Treat that green functional
result as non-qualifying gate evidence: it does not prove the required cap.
The P1 `r2` and `r3` lane configs now declare `resources.cpus = "3"`, making
Docker apply `--cpus 3` before the command starts; the refreshed live gate
must verify `NanoCpus=3000000000`. This avoids a post-launch race for the
11-second canary lane while preserving the same 3-CPU ceiling required by
RW-319. Because this config/report/log commit changes the tree, refresh both
P1 short-gate receipts on its exact commit before review. The prior R2
candidate-hung result remains incomplete under RW-322 and is not changed by
this ruling.

### RW-324 — 2026-09-25 11:34:01Z — resume P1 after interrupted Sol review

The controller resumed from clean P1 worktree `rg55-p1-private-ns` at
`f76f1f0154615547d8828a15e28f93a597915009`; main is
`031c37ddc615545aa819e722e160c877d84157ea`. This candidate includes the
interrupted Sol review's two committed follow-ups (`e93a40fc` runtime project
metadata in the image, `f76f1f01` corrected shared-cgroup attribution), but no
round-3 report or verdict exists. These commits invalidate the earlier
`ec288209` short-gate receipts, and main's Assay B100 merge requires current-
main reconciliation before new registered evidence.

The interrupted live review observed that helper-mode `pid:N` is translated
to a container-id/subpath spec, which may lose the PID identity that enables
per-process sampling and DAMON attribution. Treat this as an unresolved
behavioral defect until an oracle proves the original `pid:N` identity is
preserved across the private namespaces, or the target is rejected explicitly
before sampling; silently broadening it to the whole container is forbidden.
The next order is: reconcile P1 with current main; implement and test this
bounded behavior; refresh exact-tip `r0-r1` and `r3` evidence with the live
3-CPU/cgroup-parent checks; then continue the unfinished third Sol review
round using rounds 1-2 and the interrupted findings. No P1 merge or release
is implied by this ruling. The old P1 R2 at `1908316b` remains
`BUDGET_EXCEEDED/CANDIDATE_HUNG`, not a pass; run the replacement in an
isolated quiet CIU worktree after provisional integration, per RW-296.

Safety note: the interrupted reviewer disconnected and removed
`cgroup-profiler-1299a7-network` while `dstdns-devcontainer-vb` was attached
to it. The devcontainer remains running with its other networks. The
controller will not recreate the network or alter any pre-existing
container/network attachment without an operator-directed, provenance-backed
recovery; future live probes may mutate only uniquely named resources created
by that probe, and must leave shared cockpit containers untouched. No file
under `/workspaces/dstdns` was read or changed. No gate or mutation process is
currently running; host memory `full avg10=0.00`, and host systemd confirms
the `dev-gates.slice` unit is loaded with `CPUQuotaPerSecUSec=5s`.

### RW-325 — 2026-09-25 12:10:10Z — P1 R0/R1 requires resolver test completion

P1's fresh `r0-r1` on quiet tree
`8df969674b343da17305a85a8e0b466d257f8f27` launched in a gate container
under the loaded `dev-gates.slice`; live inspection confirmed
`NanoCpus=3000000000`. The lane exited 2 because the coverage threshold is
100%: the new `lib/targets.py` resolver remained at 96% line coverage, with
uncovered branches in malformed/missing proc facts and identity-match refusal
paths. The focused 220-test local suite passed but does not satisfy the
changed-line line-and-branch bar. The next commit must add behavioral oracles
for every changed branch; rerun exact-tip `r0-r1` and `r3` after that commit.
No merge or Sol review is authorized by the failed gate. The contract mirror
remains byte-identical.

### RW-326 — 2026-09-25 12:30:19Z — detached CIU campaign identity is intentionally refused

The reported `ciu worktree` refusal is not a new CIU behavior or a controller
mutation. `.worktrees/rg55-p6-r2-ciu` is Git-detached at
`aae66356bf3a65ef8b3ba7fa04a8042f2feee55c`, while its generated
`ciu.worktree-instance.json` still claims branch `rg55-p6-r2-ciu`. CIU's
`list_instance_records` cross-checks the recorded branch against Git and
refuses a contradictory family identity by design. The checkout was detached
to honor assay's exact-tree resume identity. Leave both checkout and generated
record untouched; create the next CIU gate worktree from an isolated local
clone with its own Git family. No CIU implementation or Docker network action
is authorized by this ruling.

### RW-327 — 2026-09-25 12:30:19Z — P1 helper-PID coverage repair is test-only

Test-only commit `d201537328ba8574968e8e1e9ff1ab8ad931f1f5` adds deterministic
oracles for missing/malformed proc facts, each PID/cgroup identity filter,
PID reuse, ambiguous/absent matches, reserved-option misuse, and the
caller-to-helper target conversion. Its load-niced focused run passed 260
tests; branch-aware coverage measured `lib/targets.py` at 441/441 lines and
220/220 branches, and all changed helper-PID lines/branches in `cgprofile.py`
were covered. No implementation changed. The focused local run is not an
authoritative package gate; after this ruling is committed, rerun P1 `r0-r1`
and `r3` on the final exact tip with loaded-slice and 3-CPU evidence. The old
R2 remains `BUDGET_EXCEEDED/CANDIDATE_HUNG` per RW-322.

### RW-328 — 2026-09-25 12:35:52Z — P1 short gates pass; record before final rerun

Fresh short gates passed on clean exact code tree
`43daf53e09f15227cacdcd80156dcdecc7cc19a6`: R0/R1 exit 0 in 75.396 s
with 5,021/5,021 statements and 1,732/1,732 branches covered; R3 exit 0 in
11.132 s with all seven canaries rejected and none surviving. The R0/R1
test container `cgprofile-gate-279657-1790339528` and R3 container
`run-gate-vbpub-r3-284762-1790339665` both reported the loaded
`dev-gates.slice` parent and `NanoCpus=3000000000`. The tree was clean and
unchanged throughout; the daemon itself was down, so run-gate reported coarse
rusage profiling for these gates. This log/report/handoff update changes HEAD;
repeat both short gates on the resulting final P1 tip before Sol review. These
receipts do not change the old R2 terminal or substitute for fresh live
review probes.

### RW-329 — 2026-09-25 16:10:42Z — P1 reconciled current main; provisional review may precede R2

Shared `main` advanced to `e5e9b95c5ac8be3452c93f1066f9436347f862fd`
after the P1 private-namespace candidate was prepared. A read-only path
comparison showed the intervening main changes did not modify
`scripts/cgroup-profiler/` or `run-gate-project/` implementation paths. The
clean P1 branch was reconciled with `git merge --no-ff main`, producing
`d108ebb2a014ac204d6c50a3b82d65471e8ada7d`; its merge base is now the exact
current main tip. On this clean tree, `r0-r1` passed 1,377 tests in 94.06 s
with 5,021/5,021 statements and 1,732/1,732 branches, and `r3` passed with
all seven canaries rejected and zero survivors. Run-gate history independently
records PASS/exit 0 for both lanes on that hash. The R0/R1 container was
`cgprofile-gate-572739-1790352189`; R3 was
`run-gate-vbpub-r3-577036-1790352338`. Both used loaded `dev-gates.slice`
with `NanoCpus=3000000000`; the daemon was down and R0/R1 used coarse rusage.

The old R2 on `51198f2e` remains FAIL with ten real oracle gaps, repaired in
the current candidate, but no replacement R2/full gate is complete for this
tree. Per the operator's workflow change, a fresh Sol ACCEPT plus the short
gates permits a **provisional merge** so other RG-55 work can proceed. This
does not permit release or shipment. Launch the replacement R2 and full gate
in an attached, separate CIU worktree after provisional merge; keep its HEAD
quiet, and backport/rejudge any fixes. The updated Sol packet distinguishes
review acceptance from mutation/release evidence. The R2 and full-gate status
remain open until their exact-tree records are read separately.

### RW-330 — 2026-09-25 16:57:50Z — P1 accepted and provisionally merged; R2/full gate remain release blockers

The fresh final reviewer was launched with `CODEX_HOME=/home/vscode/.codex2`,
`--model gpt-6-sol`, and `model_reasoning_effort="xhigh"`. The persisted
Codex `turn_context` independently records `model=gpt-6-sol`, `effort=xhigh`,
and the P1 worktree cwd. Review round 3 ACCEPT is committed at
`b0544d50a5b3151bc218236a411d9d38e31a8791`; report:
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REVIEW-round3.md`.
It found B1: explicit empty/whitespace `CGPROFILE_VERSION` had collapsed into
the absent-value development default. Fix `158488ccbae418bba6b4022e8c9295742a6f9885`
now validates any present value, with four build/publish regression cases.

Exact short-gate evidence is clean: R0/R1 and R3 passed on code tip
`158488cc` (1,381 tests; all seven canaries rejected), and both lanes were
then independently rerun on the report-only tip `b0544d50`. The final R3
container was `run-gate-vbpub-r3-656577-1790355222`, under loaded
`dev-gates.slice` with a 3-CPU cap. The clean branch was provisionally
merged with `--no-ff` as `d5d53afc8f72c50c01ade684dd84bfeee2f946ac`;
its tree is identical to the reviewed/gated `b0544d50` tree.

This merge is not release evidence. No replacement P1 R2 or full gate has
passed; the older R2 records remain invalid for this tree. Run them in a new,
isolated CIU clone/worktree because the root CIU family still has the stale
P6 identity described in RW-326. Keep the attached campaign HEAD quiet and
use the pre-merge `origin/main` base `e5e9b95c5ac8be3452c93f1066f9436347f862fd`
to measure the P1 delta; after resolving the base once, do not change it.
The current R2 config uses `base = "origin/main"`, `jobs = 2`, and a
4-hour resumable campaign. Any fix must be backported and judged on the
resulting exact tree before release.

The review's live ephemeral/shared/helper probes succeeded for memory
sampling, HTML report creation, and private-namespace helper-PID identity.
The host kernel rejected DAMON `kdamond_commit` with `EINVAL`; no live DAMON
series or overhead measurement was obtained. Record this as an open P3
close-out item, not as passed DAMON evidence. The final host daemon must still
be brought up and verified through CIU after release.

P2's old detached retry instruction is obsolete: current `main` already
contains the run-gate 23.7.0 integration (`a921100d`). Two attempted
background wrappers for the old `186461de` checkout produced no exit marker,
no new history entry, and no changed Assay progress timestamp; no new P2
mutation run occurred. Do not resume that stale tree with today's `main` ref.

The host `host-escape systemctl show dev-gates.slice` check confirmed
`LoadState=loaded` and `/dev.slice/dev-gates.slice`. `host-escape` also
reported restoring the expected cgroup2 mount options; no tracked host-setup
source was changed.

### RW-331 — 2026-09-25 17:08:09Z — isolated P1 R2 family prepared; preserve shared jobs and network state

To bypass RW-326 without touching its contradictory detached P6 checkout, an
isolated local clone was created at
`.worktrees/rg55-p1-r2-isolated`; its managed CIU worktree
`.worktrees/rg55-p1-r2-isolated/.worktrees/rg55-p1-r2-isolated` is attached,
clean, and at `f0fccaf6368c18d32c401574b2819075986249b6`. In the isolated
clone only, `refs/remotes/origin/main` is pinned to the exact pre-wave base
`e5e9b95c5ac8be3452c93f1066f9436347f862fd`; do not fetch/advance that ref.
The candidate branch must remain quiet after its final tip is selected.

CIU's `worktree create` also created the unique network
`rg55-p1-r2-isolated-2be782-network` and attached the existing shared
`dstdns-devcontainer-vb` cockpit container to it. This side effect was not
visible in the command help; leave that attachment and network untouched.
No file under `/workspaces/dstdns` was read or changed, and no prior network
or container was detached, removed, or rewritten.

At 17:06Z, read-only inspection found two unrelated campaign containers in
the loaded `dev-gates.slice`: CMRU mutation
`run-gate-vbpub-mutation-439942-1790345741` and assay
`run-gate-vbpub-assay-224793-1790338344`. Both reported `NanoCpus=0`; the
slice quota is 5 CPUs and host memory PSI `full avg10` was 0.33. The
controller did not mutate either container. The two available agents were
asked to identify ownership and apply the exact-name 3-CPU cap. One mutation
lane is active; starting P1 R2 would be the second mutation lane and remain
within the current three-lane estate limit. Recheck slice/PSI immediately
before launch and verify P1's exact container cap after launch.

### RW-332 — 2026-09-25 17:29:55Z — P1 R2 progressing; P6 current-main gates green, review still pending

P1 R2 started at 17:16:39Z on quiet tree
`450fe53d0baca81ec5d32432c6c47117862fa992`, with the isolated clone's
`origin/main` fixed at `e5e9b95c5ac8be3452c93f1066f9436347f862fd`. The
90-second check found the exact container
`run-gate-rg55-p1-r2-isolated-r2-693694-1790356599` running under
`dev-gates.slice` with `NanoCpus=3000000000`. Assay had recorded its baseline
and candidate 0 as killed (1/121); host memory PSI `full avg10=1.25`, below
the launch ceiling. The plan's 10-hour estimate is the 600-second-per-mutant
worst case; the closest previous P1 run judged 81 candidates in 74 minutes.
The last startup progress event was at 17:18:19Z; next check is no earlier
than 17:43:19Z, unless an expected early completion or error appears.

P6 branch `rg55-followups-cgprofile-final` reconciled current `main` in
`41c6fba6d712fbf70d4d11ba5f37cb22a919b733`. The merge retained main's RW-312
and RW-313 once, P6's RW-314..RW-317, and main's RW-318..RW-331. Its exact
R0/R1 run passed in 140.682 s (exit 0): 1,651 tests; 5,994/5,994 statements
and 2,056/2,056 branches. Container `cgprofile-gate-704906-1790357010`
reported loaded `dev-gates.slice` and `NanoCpus=3000000000`; the daemon was
down, so sampling was coarse `rusage-maxrss`. Exact-tree R3 also passed in
13.994 s (exit 0), rejecting all seven canaries. Its creation argv included
`--cpus 3`, but the container exited before a follow-up `docker inspect`; a
fresh short R3 with a live cap observation is required before provisional
merge.

P6 has no current-tree R2 evidence. Its older `aae66356` run remains
`BUDGET_EXCEEDED/LANE_TIMEOUT` (362 candidates: 312 killed, 12 survived, 38
budget-exceeded); that result and its survivor repairs are documented in the
P6 REPORT but do not qualify the reconciled tree. Per RW-296, the short gates
plus fresh Sol xhigh review may authorize provisional integration only; P6
R2 and the full gate remain mandatory before release. For the post-merge P6
R2 clone, resolve `origin/main` to the exact P1 baseline (current main before
P6 integration), not the stale repository remote-tracking ref at `e5e9b95c`;
record the resolved base from the run verdict. The P3 DAMON live-series and
overhead measurement remains open from RW-330.

### RW-333 — 2026-09-25 17:41:46Z — P6 review permits provisional integration, not release

P6's current candidate is on branch `rg55-followups-cgprofile-final`,
worktree `.worktrees/rg55-followups-cgprofile-final`, reconciled through main
`4d32bcfe`. The older P6 R2 at `aae66356` remains
`BUDGET_EXCEEDED/LANE_TIMEOUT` and is not evidence for the current tree. Its
R0/R1 and R3 receipts at `41c6fba6` are also preliminary; final exact-tip
registered R0/R1 and R3 are required, and R3's exact container must be
live-inspected for `NanoCpus=3000000000` under loaded `dev-gates.slice`.

Per RW-296, after those exact-tip short gates, 100% changed-line and branch
coverage, the P6 handoff's required live probes, and a fresh independent
GPT-6-Sol xhigh ACCEPT, merge P6 with `--no-ff` provisionally so RG-55 work
can continue. A pending exact-tree R2/full gate alone is not grounds for Sol
to reject code review. This is not a release decision: cgprofile 1.1.0,
installation, and `ciu up` remain blocked until a complete acceptable current-
tree R2 with survivor disposition and the registered full gate pass, along
with all other wave close-out requirements. BRIEF-11 and both P6 review
packets encode this boundary; their commit changes the candidate tree, so the
short gates must run after that commit and the tree must stay quiet throughout.
### RW-335 — 2026-09-25 21:11:56Z — install and verify the authored D-29 daemon slice

The host check for P6's required daemon containment reported
`cgprofile.slice` as `LoadState=loaded` but with empty `FragmentPath` and
`ControlGroup`, plus unlimited `MemoryMax` and `TasksMax`. This is the
systemd auto-vivified, unbounded placeholder explicitly covered by D-29; it
cannot qualify the P6 live review or daemon deployment.

The P6 candidate already contains the authored
`scripts/cgroup-profiler/infra/cgprofile.slice` unit. The controller will
install that exact file at `/etc/systemd/system/cgprofile.slice`, reload
systemd, and verify the loaded fragment, control group, and bounded resource
properties through `host-escape`. This is the documented operator deployment
step, not a source repair and not a change under `/workspaces/dstdns`.
P1's exact-tree R2 campaign remains untouched while this host prerequisite is
established.

### RW-336 — 2026-09-25 21:17:50Z — resume P1 R2 after externally terminated attempt

The P1 R2 attempt on exact tree `429c076369b20587e395739892b3e1b715cd3482`
was terminated with exit 143 at candidate 85/121. The run-gate receipt records
no OOM kill and no verdict for that attempt; its existing verdict remained the
earlier diagnostic tree's result, so the interrupted attempt is not mutation
evidence. Its progress stream is resumable and was left intact.

After a fresh PSI check (`memory full avg10=0.05`), the controller resumed the
same exact tree with `./run-gate.py r2`. The 90-second acceptance check found
container `run-gate-rg55-p1-r2-isolated-r2-1072906-1790370942` running under
`dev-gates.slice` at `NanoCpus=3000000000`, with progress active and launch PSI
`full avg10=0.01`; no HEAD or judged worktree mutation occurred. The resumed
campaign is the authoritative attempt pending its final verdict.

### RW-337 — 2026-09-26 00:19:39Z — P1 R2 completed with liveness budget failures; P6 placement blocker exposed

The resumed P1 R2 run on exact tree
`429c076369b20587e395739892b3e1b715cd3482` completed at 21:56:38Z with
`BUDGET_EXCEEDED/CANDIDATE_HUNG` (exit 4): 121 candidates, 114 killed, 7
hung, 0 survived, 0 crashed, 0 budget-exceeded. The seven hung candidates are
all in `lib/targets.py`; the receipt measured 395.4 seconds of memory-full
stall and 836 MiB peak. This is not release evidence and must be rejudged or
otherwise explained without treating host contention as product behavior.

The Sol continuation of P6 round 3 is recorded in
`cgprofile-P6-FOLLOWUPS-REVIEW-round4.md` and committed as `ca8fccc7`; it is
`REJECT`. Blind live probes found B1 (single-session status omitted the
contract timestamp), B3 (watch certified a failed SIGKILL as `killed`), and
B2 (the private-PID daemon's host PID writes to `cgroup.procs` fail with
`ESRCH`, leaving the placed leaf empty while reporting success). The reviewer
committed the bounded B1/B3 fail-closed repair `9c0a6e39`, but explicitly left
B2 as the critical placement-design blocker. No P6 merge, release, or mutation
campaign is authorized until placement is either safely implemented while
preserving D-15 private namespaces or the contract is changed by a recorded
product ruling.

### RW-338 — 2026-09-26 00:24:01Z — namespace-safe P6 placement bridge selected

The controller selects the D-15-compliant repair for P6 B2: retain private
PID, cgroup, and network namespaces; keep the existing guarded cgroupfs leaf
creation/cap/kill writes; and, only when a direct leaf `cgroup.procs` write
returns `ESRCH`, call host systemd's `AttachProcessesToUnit` over the daemon's
explicit read-only system-bus socket mount. The call names only the verified
gates slice, the session's `rg-<token>` subcgroup, and one resolved host PID.
The daemon then verifies that PID's host-proc cgroup path equals the leaf.

An attach failure or failed read-back is a placement refusal, never a success
with an empty leaf. This uses systemd as the host PID-namespace authority
without putting any RG-55 container in a host namespace, adding a Docker
socket, or broadening the D-25 write whitelist. P6's Docker image therefore
supplies `busctl` and its compose stack mounts only the host system bus socket
read-only. The contract/design/README/consumer docs and focused oracles are
updated together on the P6 repair branch.

### RW-339 — 2026-09-26 18:16:08Z — P6 exact-tree R2 completed; two oracle repairs required

The replacement P6 mutation campaign on quiet tree `b3df5602` completed at
`2026-09-26T05:53:00.824604+00:00`: 312/312 candidates accounted for, 300
killed, 12 survived, and zero equivalent, hung, crashed, or
budget-exceeded. The separately read verdict is `FAIL/MUTANTS_SURVIVED`, exit
1, after 13,518.851 seconds. Survivor triage accepts ten as contract
equivalences and identifies two real oracle gaps: the non-ESRCH private-PID
placement bridge guard and fail-closed PID identity on an `OSError`. Focused
behavioral tests cover both in P6 repair commit `6540f877` on
`rg55-followups-cgprofile-final`; the b3df5602 mutation receipt is diagnostic
only, and fresh final short gates plus one replacement R2 are required before
P6 release. P1's fresh Sol reviewer is concurrently running its own final
gates; do not launch the P6 gate container until that gate slot is free.

### RW-340 — 2026-09-26 18:22:23Z — P6 replacement R2 launched asynchronously

After the P6 test-only repair, the controller created a quiet detached
worktree at `6540f87761a66ff933c8bb45f81d8ac9117f407b2` and launched the
registered `r2` lane under a `setsid` owner PID `1190416`. The exact container
is `run-gate-vbpub-r2-1190416-1790446819`; the 90-second acceptance check
found it `running` in `dev-gates.slice` with `NanoCpus=3000000000`. Its
progress stream is in the baseline pytest phase, with no verdict yet. The
previous P6 campaign's measured rate gives an estimate of roughly 3h45–4h;
the owner and container are left untouched between 25-minute-or-longer
observations. P1's repaired R2 remains the other mutation lane.

### RW-341 — 2026-09-26 23:06:22Z — P1 R2 passed; P6 replacement R2 did not qualify

The separately read P1 verdict on commit `1080ac2f068732dcd490bcc2c5cefe56db8ec805`
is `PASS`, exit 0: 125/125 candidates killed, no survivors, hung candidates,
or budget-exceeded candidates. This is valid exact-tree R2 evidence for that
tree. The previous Sol reviewer process ended at the account usage limit
before writing a final review report; rounds 1–3 do not cover the repair
commits `429c0763`, `0a2e0cd8`, and `1080ac2f`, so a fresh final review and
registered short gates remain necessary.

P6's replacement R2 on `6540f87761a66ff933c8bb45f81d8ac9117f407b` ended
`BUDGET_EXCEEDED/CANDIDATE_HUNG`, exit 4, with 312/312 candidates accounted
for (301 killed, 10 survived, one hung). The ten survivors match prior
contract-equivalence dispositions; the two real gaps were killed. Candidate
`0a38e7d8…` at `lib/liveness.py:530` was classified hung after 138.953 seconds
and 675 completed tests. Its run profile also records 135.1 seconds of
memory-full stall, but does not preserve the active pytest node or establish
causation. P6 mutation evidence is not passing; preserve the candidate state
and investigate the hang on the reconciled final tree.

At the next launch check, host memory PSI was `full avg10=10.50`, above the
RG-55 launch limit of 5. The subsequent P1 r0-r1 launcher's own preflight
observed `full avg10=0.0`, `avg60=0.4%`; its placement probe verified the
loaded `dev-gates.slice`, and its exact test container ran with
`NanoCpus=3000000000`. Do not generalize the earlier high reading to later
launches; use each launcher's current preflight.

### RW-342 — 2026-09-26 23:13:09Z — P1 r0-r1 passed on 1080ac2f

The registered lane started at `2026-09-26T23:09:07Z` and recorded PASS at
`2026-09-26T23:10:47Z` (100.26 seconds, exit 0) on clean commit
`1080ac2f068732dcd490bcc2c5cefe56db8ec805`. The exact test container
`cgprofile-gate-1356625-1790464149` ran under `dev-gates.slice`; the launcher
verified `NanoCpus=3000000000`. The suite reports 1,398 passed and 4 warnings.
Coverage reports 5,026 statements and 1,738 branches with 100% on both.
Profiling used the basic `rusage-maxrss` fallback because the daemon was not
running; peak was at the 38,469,632-byte floor. The separately read
`.run-gate/history.json` records this lane PASS, `dirty=false`, and revision
46. P1 R3 is now running at commit `1080ac2f`; its detached owner is recorded
in `/tmp/rg55-p1-r3-1080.pid`.

### RW-343 — 2026-09-26 23:20:36Z — P1 r3 passed on 1080ac2f

The registered `r3` lane completed at `2026-09-26T23:12:23Z` on clean
commit `1080ac2f068732dcd490bcc2c5cefe56db8ec805`; its separately read
`.run-gate/history.json` receipt records PASS, exit 0, `dirty=false`, and
revision 46. Seven canaries were rejected and zero survived. Launch PSI was
`memory full avg10=0.03%`; the exact invocation declared
`--cgroup-parent dev-gates.slice` and `--cpus 3`, and used unique container
`run-gate-rg55-p1-r2-isolated-r3-1360087-1790464331`. The daemon was down,
so this lane did not supply live-daemon or DAMON evidence. Together with the
R0/R1 and R2 receipts recorded above, this clears P1's exact-tree mutation and
registered-lane gates for the fresh final review; the already-provisionally-
merged daemon still needs full-gate verification before release.

### RW-344 — 2026-09-26 23:42:10Z — P1 final review active; P6 evidence gap filed upstream

A fresh P1 reviewer is running in persistent command session `19740`, thread
`01a0e018-a3bb-7662-9c0e-1633b7b3f54b`, rooted at the isolated candidate
worktree. Its saved turn metadata independently confirms model `gpt-6-sol`,
effort `xhigh`, and the expected worktree. An earlier fresh attempt ended
after emitting only its route-check message and made no repository call; it
produced no review record and did not change the candidate. The current review
must cover `<f0fccaf6>...1080ac2f`, reconcile rounds 1–3, and complete the
P1 handoff's live probes before acceptance.

The P6 exact-tree campaign `6540f87761a66ff933c8bb45f81d8ac9117f407b`
remains nonpassing (`BUDGET_EXCEEDED/CANDIDATE_HUNG`). The earlier `b3df5602`
campaign killed the same candidate with a named test witness; the trees differ
by two added P6 tests, so this is not a controlled replay and does not prove
host pressure caused the later hang. Assay backlog B107 was filed on main at
`4241cdc1` and qualified at `659a92a5`, preserving that uncertainty and
requiring time-aligned candidate resource evidence; neither campaign is
reclassified.

P3 static closeout check: `run-gate-project/run-gate.footprint.json` is
tracked; SPEC-V8 Appendix D.6 is present; RG-55 is marked FIXED and RG-56 / RG-57
are filed (RG-56 remains OPEN, RG-57 is FIXED). The final RG-55 report does not
yet exist and the adoption brief's ⟨P3⟩ fields remain unfilled until live probes
and DAMON overhead are measured. At this check, the exact external container
`run-gate-assay-selfhosted-1372147-14760-1790465048` was active for Assay B105;
it is outside this wave and remains untouched.

### RW-345 — 2026-09-26 23:46:32Z — host cgroup mount repair matched checked-in policy

The read-only `host-escape` inspection runs `mdt doctor` as its preflight. It
found the host cgroup2 mount missing `nsdelegate`, `memory_recursiveprot`, and
`memory_hugetlb_accounting`, then restored those flags through the existing
`CGROUP2_FLAGS=fix` policy. The same behavior is present in the checked-in
`modern-debian-tools-python-debug/host-setup/scripts/mdt-dev-governance-reconcile.sh`;
no additional host-setup code change is needed. The host reports
`dev-gates.slice` loaded with `CPUQuotaPerSecUSec=5s`. The host
`/run/cgprofile/ctl.sock` and cockpit-visible `/run/cgprofile/ctl.sock` are
currently absent because the daemon is down and the devcontainer has no
socket mount. Do not touch `/workspaces/dstdns`; reassess the socket carrier
after the main daemon is up and use an explicitly mounted reviewer-owned probe
client if feasible.

### RW-346 — 2026-09-27 02:43:04Z — P1 final review conditional; exact-tree R2 resumed in a mapped worktree

The caller verified the fresh review session's saved invocation metadata as
`gpt-6-sol` / `xhigh`; the reviewer was not required to self-attest. Round 4,
recorded at `scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REVIEW-round4.md`
on `9933efb1`, is `ACCEPT-conditional`. It repaired malformed proc-stat PID
identity and explicit-null DAMON selection (`248a29ba`, `cc7e1910`), and
records reviewer-owned daemon/helper probes. R2 on old tip `1080ac2f` is not
evidence for those repairs. The first fresh R2 attempt used a sibling
worktree outside this nested checkout's configured bind-mount and failed
before mutation with a missing in-container path; no candidate ran. That
attempt is not evidence and its container was not retained by Docker.

A second detached worktree was placed beneath the configured mounted clone at
`.worktrees/rg55-p1-r2-isolated/.worktrees/rg55-p1-r2-final-20260927`, exact
code tree `cc7e191074a94c53e92023ec4feb75fdf753bae4`. The registered `r2`
lane is active in container
`run-gate-rg55-p1-r2-isolated-r2-1595444-1790476701` (launcher PID 1595444),
`dev-gates.slice`, `NanoCpus=3000000000`. The 90-second health check saw the
baseline finish at 94 seconds and candidate 1/125 killed at 107 seconds; the
tree remains quiet. The prior 125-candidate P1 R2 used 6,255 active seconds,
so the rough comparable runtime is about 1h45. Do not inspect progress again
before the 25-minute observation interval unless an expected failure signal
appears. A full release gate and P1 release remain pending.

P6 is not resolved by increasing the lane-wide budget: its prior exact-tree
R2 ended on one `CANDIDATE_HUNG`, and ordinary resume retains that terminal
record. The registered run-gate assay argv supplies `--resume`, progress, and
state-dir but has no supported `--rejudge-outcome` forwarding. Keep B107's
resource-causality question open; do not reinterpret the result or hand-edit
assay state. P3 remains open: no host singleton/socket is running or mounted
in this cockpit, and round-4 live probes could not allocate DAMON (`EINVAL`),
so live DAMON behavior and measured overhead are not yet established.

### RW-347 — 2026-09-28 02:31:50Z — P1 exact-tree R2 completed with one parser survivor

The later registered P1 R2 in the isolated mounted clone completed on exact
clean tree `cc7e191074a94c53e92023ec4feb75fdf753bae4`. Read the verdict,
progress stream, and run-gate history separately: all 125 candidates were
accounted for (124 killed, 1 survived; no equivalent, budget-exceeded,
crashed, or hung candidates), and the verdict is `FAIL/MUTANTS_SURVIVED`,
exit 1. History marks this exact-tree run clean and eligible; duration was
5,534.45 seconds. This is not release-pass evidence.

The survivor is `scripts/cgroup-profiler/lib/targets.py:288`, `Lt->LtE`, in
`proc_start_time_ticks`. A controller live probe successfully set a child
process name to the empty string with `PR_SET_NAME`; the kernel exposed its
stat record as `pid () S ...`. The existing `<` accepts the empty command
name and parses its start-time field; the mutant rejects it. A new regression
`test_start_time_accepts_an_empty_process_command_name` was added on a
separate branch/worktree based on `cc7e1910`. No gate or mutation run has yet
judged that follow-up tree. Keep the exact `cc7e` R2 artifacts unchanged,
commit the new test and triage record, then run fresh short gates and R2 on
the quiet follow-up tree; preserve the Sol round-4 session for fix-verification
if it remains available.

### RW-348 — 2026-09-28 03:05:00Z — B107 load-dependent CLI fixtures removed; P1 fix accepted

The first B107 `tester-unified` run on `115eb94873c569e1bc7d6b57f03dfc6a317e7b0f`
was clean and history-eligible but failed after 749.391 seconds: 5,163 passed,
21 skipped, two failed. One CLI-level mutation fixture expected
`CANDIDATE_HUNG` after a real 50-second wall deadline; observed external
pressure paused B107's eligible liveness clock, so the correct bounded result
was `LANE_TIMEOUT`/incomplete. The fixture's pass/fail therefore depended on
host scheduling and contradicted B107's explicit deterministic-test contract.
The other failure was a stale `CommandResult` import flagged by pyflakes.

Commit `612843ef` on `rg55-assay-b107-r1` removes the two real-deadline CLI
mutation fixtures, updates the bucket-test coverage map, and removes that
unused import. The same suite's monitor tests inject clocks, process
observations, and pressure; mutation bucket/evidence persistence remain tested
separately. Targeted local tests passed (85 passed). A replacement registered
`tester-unified` run is active in container
`run-gate-assay-selfhosted-2621167-9451-1790564672`; do not treat it as green
until its terminal verdict and history are read separately.

P1's empty-`comm` survivor repair at `e55a547cf6eec3a19242e3488fd4aae73e177b28`
received Sol round-5 `ACCEPT`; the report is committed as `0080eba7` on the
P1 fix branch. This acceptance covers the repair only. Fresh short gates and
R2 still need to judge the resulting committed tree before release.

### RW-349 — 2026-09-28 03:22:09Z — P1 R2 attempt stopped after baseline drift narrowed scope

The controller launched the P1 R2 lane on `0080eba7f91128d4df2d50368b7edaf1465f7805`
in the prepared CIU worktree. At the 90-second check it was healthy under
`dev-gates.slice` with `NanoCpus=3000000000`, and the baseline completed. Its
candidate plan unexpectedly selected only five mutants. The separately read
prior full R2 verdict resolves its base to
`e5e9b95c5ac8be3452c93f1066f9436347f862fd` and judged 125 mutants. The nested
clone's `origin/main` had since advanced to `fb9d8f5b` (reflog: fetches at
2026-09-27 23:45:12Z and 2026-09-28 00:20:44Z), contrary to RW-329/RW-330's
fixed-base instruction. The new plan consequently measured only a small
post-base diff and is not a valid replacement for P1's full campaign.

The controller stopped only its exact container
`run-gate-rg55-p1-r2-isolated-r2-2634647-1790565019`; run-gate history records
exit 143 on the clean tree, and no final assay verdict file exists. The
partial baseline/two-candidate progress and profiling artifacts are preserved
in that worktree and are not mutation evidence. No unrelated container or
worktree was changed. The isolated clone's remote-tracking ref was restored
with an old-value-checked ref update to the planned `e5e9b95c` base.

A new CIU worktree `rg55-p1-r2-pinned-20260928` is ready at the same clean
`0080eba7` tree; both `origin/main` and its merge base resolve to `e5e9b95c`.
Read-only `assay plan r2` confirms the intended 125 candidates (40 access, 4
serve, 1 summary, 72 targets, 8 version; operators: 61 compare, 34 boolop, 5
bool-constant, 25 falsy). The planned campaign is not launched until the
memory-PSI admission threshold is met; the aborted run ended with host
`memory full avg10=24.84%` and 1-minute load average 10.06. CIU could not
allocate its optional per-instance Docker network because the address pools
are exhausted; the checkout itself is ready and the assay lane does not use
that network.

### RW-350 — 2026-09-28 03:42:51Z — controller resumed; B107 needs current-main integration

The controller resumed from the persisted checkpoint. The shared root checkout is
at `790e31d3`, locally ahead 38 / behind 5 against its already-present
`origin/main` ref, with operator-owned `AGENTS.md` edits; that file is preserved.
Current local main includes Assay B101 P1 (`36f8551c`) and B101 P2
(`5bf832a4`). B107's replacement registered `tester-unified` run completed
PASS on `612843ef` (clean and history-eligible, 1,048.61 seconds); the earlier
RW-348 sentence saying that run remained active is stale. The green receipt is
for a tree based on `fb9d8f5b`; B107 changes `assay/src/assay/mutation.py`, so
that receipt and any review of the old tree are not merge evidence for current
main. Preserve the old tree and integrate B107 into a fresh worktree from
current main, retaining the B101 changes before review/gates.

P1's prepared CIU worktree is
`.worktrees/rg55-p1-r2-isolated/.worktrees/rg55-p1-r2-pinned-20260928`, exact
HEAD `0080eba7f91128d4df2d50368b7edaf1465f7805`, clean, with its nested
`origin/main` pinned to `e5e9b95c5ac8be3452c93f1066f9436347f862fd`. Its
`assay.toml` uses `judge.base = "origin/main"`; a read-only plan on the exact
tree reports the intended 125 candidates. `run-gate.py --dry-run` confirms
`dev-gates.slice`, `--cpus 3`, the mounted CIU worktree, and the expected assay
command; no container was started. Passing `--base` is correctly refused
because this command lane does not delegate a `{base}` token, so the run will
use the lane's declared, pinned `origin/main`. At preflight, memory PSI `full
avg10=0.00`; two run-gate mutation containers were active, leaving the
operator-authorized third slot available.

### RW-351 — 2026-09-28 03:59:17Z — P1 R2 and current-main B107 gate launched

The first detached P1 launcher returned without creating a run-gate history
entry, assay progress/verdict, or container; it is not a test result. The
campaign was restarted under retained command session `79863`. Exact container
`run-gate-rg55-p1-r2-isolated-r2-2711042-1790567389` started at
`2026-09-28T03:49:49.712Z`, on the clean pinned tree `0080eba7`, with
`CGROUP_PARENT=dev-gates.slice` and `NanoCpus=3000000000`. The first health
check saw the baseline complete at 79.235 seconds, 125 selected candidates,
and candidate 0 killed after 11.488 seconds. Prior exact 125-candidate P1 R2
history is 5,534.45 seconds (about 92 minutes); use that as the current
runtime estimate. Profiling reported that `cgprofile-host-daemon` is down and
used basic in-lane sampling; this campaign supplies no live-daemon or DAMON
evidence. Do not inspect its progress again before `2026-09-28T04:16:21Z`
unless a concrete failure signal appears.

The primary checkout's `ciu worktree inspect` refuses because
`.worktrees/rg55-p6-r2-ciu/ciu.worktree-instance.json` says branch
`rg55-p6-r2-ciu` while Git has that checkout detached. It was left untouched.
An isolated local clone `.worktrees/rg55-assay-b107-isolated` avoided that
stale-record scan; CIU created `rg55-assay-b107-current` at current local-main
SHA `9394b460` (fork point recorded exactly). CIU's optional Docker network
allocation failed because address pools are exhausted, but the checkout is
`ready`; no application container was started. B107 commits
`115eb948` and `612843ef` cherry-picked cleanly as `c55147fd` and `52c4a978`.
The latter tree is clean, preserves current-main B101 P1/P2, and its focused
resource/liveness/mutation tests pass (228 passed in 34.05 seconds).

The registered `tester-unified` lane is running on exact tree `52c4a978` in
`run-gate-assay-selfhosted-2732119-17705-1790567817`, started
`2026-09-28T03:56:57.803Z`. It is under `dev-gates.slice` at 3 CPUs. The
90-second health check found the wheel build and initial attestation/schema
phases progressing. It launched when host memory PSI `full avg10=0.61`; that
rose to `20.22` during the run. The test result must remain independent of
that external pressure; do not infer a verdict from elapsed time. The previous
same-lane run took 1,048.61 seconds, so the next joint observation with P1 is
planned near `04:16:21Z` unless this gate is expected to have finished sooner.

Read-only metadata identified the unrelated UUID-named container as a
production Pterodactyl game-server container in `wings.slice`; it is not a
mutation/gate slot and remains untouched. Its process listing exposed
password arguments in command-line output; do not repeat or store them, and
advise the operator to rotate those credentials.

### RW-352 — 2026-09-28 04:18:58Z — P6 B106 reuse is eligible only as witness replay; cockpit socket still absent

Read the terminal P6 R2 receipt from exact source tree `6540f877` and its
history separately. It is schema v13, native/unsharded, and accounts for all
312 candidates: 301 killed, 10 survived, 1 hung. The 301 native killed-state
records include B106 execution witnesses. After P6 reconciles P1's source
repair and the current main tree, Assay B106 may replay only those current
candidate IDs whose witnesses pass the current baseline; survivors, the hung
candidate, new/changed IDs, and every uncertain case must run fully. This is
an acceleration plan, not a pass or a substitute for final exact-tree R2.

The current cockpit has `/run/cgprofile` (mode 0770, owner 0:994), but no
`/run/cgprofile/ctl.sock`; `/sys/fs/cgroup/dev-gates.slice` is also not
visible in this namespace. The socket-carrier P3 probe therefore remains
unavailable from this cockpit as configured. This does not establish the
host daemon's state; use the authorized host path only after the daemon's
loaded bounded slice and singleton are verified.

B107's final integrated candidate is clean at `5b79fcd1` in
`rg55-assay-b107-mainline-20260928`, based on isolated reconciliation merge
`73075ec2` of local main and the fetched origin tip. Its current-main
`tester-unified` gate must run on this exact tree before Sol final review.
The registered invocation launched on the prior integrated tree `52c4a978`;
this controller has not yet read its terminal result. Do not inspect either
long-running lane's progress again before its allowed expected-completion
check.

### RW-353 — 2026-09-28 04:20:53Z — P6 prior verdict passes the B106 source preflight

Loaded the P6 `6540f877` verdict with the current Assay B106
`load_reuse_source` path (including its v13 verifier). It is accepted as a
complete, unsharded, native source with 312 candidate IDs: 301 killed, 10
survived, and 1 hung. Exactly 300 killed outcomes have valid replay
witnesses; the remaining kill has no usable witness and must run fully.
Therefore B106 is a viable way to shorten P6's post-reconciliation R2, not
an assumption that every previous kill can be reused. Before launch, reconcile
the P1 source fix and current main, preserve this source artifact unchanged,
then run `assay plan r2 --reuse-from <6540f877 verdict>` on the final candidate
tree and verify each classification. Survivors, hung outcomes, changed/new
candidate IDs, and all uncertain witness replays execute the full suite.
Final R2 and full-gate evidence remain required for release.

### RW-356 — 2026-09-28 19:48:49Z — P1 hung result preserved; B107 exact-tree gate running

P1 R2 on clean tree `0080eba7f91128d4df2d50368b7edaf1465f7805` ended
`BUDGET_EXCEEDED/CANDIDATE_HUNG`, exit 4, after 6,693.169 seconds. All
125 candidates are accounted for: 115 killed, 10 hung, and zero survived,
equivalent, crashed, or budget-exceeded. Every one of the ten hung progress
records had completed all 1,394 tests before classification; each process
then remained alive for 134.874–220.231 seconds. This is a repeated
post-suite shutdown pattern, but does not establish that host contention
caused it. Preserve the failures as unresolved and rejudge on the B107 tree.
Container memory-full stall was 242.56 seconds; host memory PSI full avg10
was 0.00 at both start and end, so the saved profile does not prove a causal
pressure relationship.

The old registered B107 `tester-unified` gate passed cleanly and
history-eligible on `52c4a978` (1,424.929 seconds, exit 0). The final
integrated candidate `5b79fcd1` now has its own registered gate running in
`run-gate-assay-selfhosted-3227502-21957-1790624585`, started
`2026-09-28T19:43:05Z`. It is in loaded `dev-gates.slice`, capped at 3 CPUs;
the 90-second health check found its exact-OID wheel installed and the
attestation/schema successor phases passing. This is not yet a terminal gate
verdict; await the next permitted completion observation before review.

The current Assay v13 reader independently verified that P1's old verdict is
a complete native/unsharded B106 source: 110 killed outcomes have usable
witnesses, five killed outcomes have no usable witness, and ten hung outcomes
require full execution. Once B107 is merged into the P1 candidate, inspect
`assay plan --reuse-from` on that exact tree; no old hung result is to be
reclassified or carried forward.

Host preflight found `dev-gates.slice` loaded as `/dev.slice/dev-gates.slice`
with `CPUQuotaPerSecUSec=5s`. An accidental `host-escape --help` invocation
ran its built-in doctor (then tried to execute a nonexistent host `--help`)
and restored missing `nsdelegate,memory_recursiveprot,memory_hugetlb_accounting`
cgroup2 mount flags. The restoration is implemented and documented in the
existing `modern-debian-tools-python-debug/host-setup` / `customization/mdt`;
the subsequent read-only host check confirmed the flags present, so no new
host-setup code change is needed. No other host or Docker object was changed.

### RW-354 — 2026-09-28 04:22:30Z — P1 R2 advances at a slower-than-prior campaign rate

At the scheduled progress observation, the exact P1 container
`run-gate-rg55-p1-r2-isolated-r2-2711042-1790567389` was still running with
`NanoCpus=3000000000` under `dev-gates.slice`. On quiet judged tree
`0080eba7`, the Assay stream had completed candidate index 31 of 125
(32 accounted), last candidate 99.962 seconds, total elapsed 1,913 seconds.
The current throughput extrapolates about 97 minutes remaining; the prior
same-size campaign took 92 minutes total, so use a broad ~1.5–2 hour
remaining estimate rather than treating the old duration as a deadline. The
next process/progress observation is no earlier than `04:47:30Z`, unless an
error or expected terminal completion justifies an earlier check. Do not
infer any verdict from elapsed time.

### RW-355 — 2026-09-28 04:25:34Z — P6 contract mirror restored before its final gate

The P6 worktree's daemon-side contract had the system-bus placement-bridge
paragraph, but the canonical run-gate mirror lacked those six lines. The
controller copied the same normative text into
`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md` in the P6 branch
as commit `5d81dcbd`; `cmp` now confirms the canonical and daemon mirror are
byte-identical and `git diff --check` passes. This docs-only commit changes
the P6 candidate tip, so P6's final short gates and fresh Sol review must
include it. No P6 campaign was running in that worktree.

### RW-357 — 2026-09-28 20:26:28Z — B107 must preserve RW-57's CPU-idle finish grace

Manual review of the integrated B107 tree `5b79fcd1` found that the
`session_finish` hang branch still tests event/output inactivity but not
candidate-tree CPU growth. The durable-evidence validator has the same gap:
its `session-finish-hang` early return checks elapsed time and the idle value,
then skips the trailing CPU-window proof. The retained B097 test
`test_session_finish_then_still_alive_is_hung_after_a_full_idle_grace` makes
the mismatch explicit: its fake process-tree CPU grows continuously while the
test expects `hung`.

Binding RW-57 says the post-finish grace expires only while candidate-tree CPU
is idle for the complete grace. Treat this as merge-blocking, not as a policy
reopening: require a complete trailing CPU-quiet window in both the live
monitor and cached-evidence validator; preserve the truly idle hang case and
add a growing-CPU post-finish case that remains incomplete at its configured
budget. Re-run focused tests, changed-line line+branch coverage, and the
registered exact-tree `tester-unified` gate after that repair, then request
fresh GPT-6-Sol xhigh review before merge.

### RW-358 — 2026-09-28 21:20:23Z — old P1/P6 campaigns are terminal; B107 review is active

The P1 R2 campaign on exact tree `0080eba7f91128d4df2d50368b7edaf1465f7805`
is terminal, not running: its schema-13 verdict is
`BUDGET_EXCEEDED/CANDIDATE_HUNG` (exit 4), with all 125 candidates accounted
for as 115 killed and 10 hung. The progress stream shows all ten hung
candidates completed all 1,394 tests before classification. Preserve this as
unresolved evidence; it predates the B107 repair and does not establish that
host load caused the hangs. No RG-55 mutation container was running at this
check.

The latest saved P6 R2 artifact is still the older exact tree `6540f877` and is
also terminal `BUDGET_EXCEEDED/CANDIDATE_HUNG` (exit 4): 312 accounted for,
301 killed, 10 survived, and 1 hung. The separate older `aae66356` retry ended
`BUDGET_EXCEEDED/LANE_TIMEOUT` with 362 candidates, 312 killed, 12 survived,
and 38 budget-exceeded. Neither artifact qualifies the reconciled P6 tree.
Two unrelated mutation campaigns were active in cmru worktrees and a separate
Nyxloom gate was active; leave all of them and their containers untouched.

The B107 integrated candidate is `f239216a`; a fresh GPT-6-Sol xhigh review is
in progress there. Its focused adversarial suite has passed (193 tests), but
the reviewer has not yet completed its combined-axis probe or written a final
report. Shared `main` advanced to `9a0d247c` with the Nyxloom session-extract
merge after the candidate's base `facbacd2`. Do not alter the reviewed
worktree while that review is active; after the review, reconcile this main
advance and re-run final-tree evidence before merge.

### RW-359 — 2026-09-28 21:40:22Z — Sol ACCEPT on B107 after repairing CPU-drop evidence

Fresh GPT-6-Sol xhigh final review ACCEPTED the repaired B107 code at commit
`5a7308d6c1c6d685850a436b86c91f0913b3b8ad`; the report is
`assay/nyxloom-trove/reports/assay-RG55-B107-SOL-FINAL-REVIEW.md` in that
candidate. The combined-axis probe found a real false `hung`: a child could
exit and lower the process-tree CPU sum, letting the earlier higher reading
be mistaken for a complete quiet window. Live monitoring and cached-evidence
validation now restart the CPU window on a drop. Resume evidence also must
derive the idle span from retained samples and show no intervening event or
output growth; malformed negative counters are unknown. The reviewer committed
these fixes and their tests/docs, with **224 focused tests passing**, six
targeted validator/monitor cases passing, and the long virtual CPU-history
oracle passing. The candidate worktree was clean after that commit.

The pre-review full-suite/coverage result does not cover this repair commit.
A fresh branch-aware line+branch coverage run is therefore active as
`rg55-b107-coverage` against that exact HEAD. At the 90-second check its exact
container was running under `dev-gates.slice`, `NanoCpus=3000000000`, and a
700 MiB cap, with pytest at 16% and host memory PSI `full avg10=1.98`. The run
uses a temporary uncommitted lane declaration and is not a release gate or
history-eligible gate result; its coverage artifact is stored under the
candidate's ignored `assay/.assay/`. After it finishes, remove only the
temporary lane declaration, preserve the artifact, and verify 100% changed
executable lines and branches before provisional merge. The registered
`tester-unified` gate and the current-tree mutation campaign remain required
after the provisional merge on a unique CIU worktree; no such RG-55 gate or
mutation container was started by the reviewer.

### RW-360 — 2026-09-28 21:44:46Z — all three mutation slots are occupied by other work

Correction to RW-358's characterization: the active Nyxloom
`session-extract` lane is itself a mutation campaign, not an ordinary gate.
At this check, exact container
`run-gate-vbpub-session-extract-3351574-1790629480` was still running; its
saved log showed candidate 102/599 at 2.6 candidates/minute, ETA about 192
minutes. Together with the two active CMRU mutation campaigns, this occupies
the current three-container mutation allowance. Do not launch an RG-55 P1/P6
mutation campaign until one of those campaigns terminates; do not interfere
with any of the three. The separate RG-55 B107 coverage lane is a non-mutation
test and remains within its own declared gate resources.

### RW-361 — 2026-09-28 23:13:43Z — B107 review accepted; exact gate evidence still needs reconciliation

A fresh caller-configured `gpt-6-sol` xhigh review ran in isolated worktree
`.worktrees/rg55-assay-b107-current-main-20260928` (review session
`01a0ea39-0242-7af2-a5ac-fc6a2257943b`). The reviewer ACCEPTED candidate
`801fa0332515bb6c34743cf133acc51fc28a2cc1`; its only new commit is the
review artifact at `415686f50be8d194b8e465fa9a000953878e88da`. The report
confirms the added cached-idle-span oracle reaches the intended rejection
branch, the intact cache record is accepted, and the inflated idle span is
refused. The four production files match the already accepted source.

The reviewer cited controller-supplied focused `tester-unified` and
changed-area coverage totals (555 tests; 326/326 changed lines and 162/162
branches), but the receipts were not independently identifiable as clean
exact-tree evidence during this check. The candidate's
`assay/.run-gate/history.json` records `rg55-b107-coverage` as exit 0 while
also recording `dirty=true` and `history_eligible=false`; the available
coverage artifacts include older runs on a different worktree. Therefore do
not merge B107 yet. Reproduce the exact candidate's short registered gate and
100% changed-line/branch judgment from a clean committed tree, retaining the
actual logs and verdict; then provisionally merge if those are green. The
reviewer's ACCEPT remains valid and no code repair is requested by that
review.

P6's new code repair is committed at
`738bf1f5cb8ea52b751078052d52a551feee485e`: stop-time placement restoration
now enumerates host-visible survivors, verifies membership, uses the exact
origin cgroup first, and falls back on `ESRCH` only through the derived
systemd unit/subgroup. Pre-commit focused tests passed 122/122 with
`lib/placement.py` at 398/398 statements and 166/166 branches. Registered
P6 gates, the live move-back/fail-closed probes, a clean current-tree R2, and
the full gate are still outstanding. P6's branch is based at `4d32bcfe`,
while shared main is `87c13eff`; reconcile after B107's clean evidence and
integration. P6's fresh Sol review series starts at round 5 after those gates
and live probes. The reusable P55 packet and P6 review handoff now make Sol
route verification the caller's responsibility; the reviewer is not asked
to self-attest.

At the process inventory timestamp `2026-09-28 22:56:51Z`, all three
mutation slots remained occupied by other work: PIDs `1573821`, `3330133`,
and `3351574`, containers
`run-gate-vbpub-mutation-1573821-1790475805`,
`run-gate-vbpub-mutation-3330133-1790628741`, and
`run-gate-vbpub-session-extract-3351574-1790629480`. No RG-55 campaign was
started; leave those runs untouched and make no routine status observation
before `23:21:51Z` absent a concrete earlier completion or error.

### RW-362 — 2026-09-28 23:20:47Z — B107 coverage reverified; allow provisional integration

The saved branch-aware coverage JSON was generated at exact code commit
`801fa0332515bb6c34743cf133acc51fc28a2cc1`; the only later candidate commit
before review was `415686f`, which adds the Sol report and no source/test
changes. Independently reran the RG-53 branch-aware judge:

```
python /workspaces/vbpub/run-gate-project/tools/coverage_gate.py \
  --coverage-json /workspaces/vbpub/.worktrees/rg55-assay-b107-current-main-20260928/assay/.assay/rg55-b107-coverage/coverage.json \
  --repo /workspaces/vbpub/.worktrees/rg55-assay-b107-current-main-20260928 \
  --base 87c13eff5b03c65f07733a282c5b1dac24609e54 \
  --source assay/src/assay
```

It exited 0: **326/326 changed executable lines and 162/162 changed
branches**. The lane-history record still says dirty and is not eligible for
duration/profile history; this explicit coverage judgment is separately
reproduced against the saved JSON and the exact base. The fresh Sol reviewer
also reports 44 behavioral and 45 documentation tests passing, plus the
controller-supplied 555-test focused run. The candidate history does not have
a `tester-unified` entry, so that aggregate test claim is retained as the
reviewer's supplied evidence rather than a registered gate receipt.

Per the operator's provisional-integration workflow, the ACCEPT, focused
behavioral/doc tests, and independently verified 100% changed-area coverage
are sufficient to merge B107 provisionally and unblock RG-55. Start the
registered `tester-unified` full gate in a fresh CIU worktree at the merged
tree, and preserve its exact logs/verdict; keep the current-tree R2 queued
until a mutation slot is free. Neither merge nor test start authorizes an
Assay release or install.

### RW-363 — 2026-09-28 23:38:31Z — reconcile P6 and launch B107's registered full gate

P6 branch rg55-followups-cgprofile-final reconciled current main
3a8bbe54068d46f34652b2ed52d19a7cddb53dd6 at merge
b53c5ffa1c55415a93f36beac63d320e478ebbfb. I resolved the contract
conflict by retaining P6's stronger stop-time behavior: verify host-visible
survivors, restore to the exact origin or a verified systemd unit/subgroup on
ESRCH, record successful events, and refuse release/leaf removal while a
survivor is unresolved. The controller-log conflict keeps P6 RW-333 and the
newer main entries through RW-362. The root and daemon-side contract copies
are byte-identical, and the P6 reconciliation merge is committed. The shared
root AGENTS.md remains untouched.

The first detached B107 gate wrapper disappeared before launching any runner
or container; all its output files were empty. No verdict was produced. After
rechecking host PSI and the loaded gate slice, the registered
assay/run-gate.py tester-unified gate was relaunched at 23:36:14Z from
isolated CIU worktree
.worktrees/rg55-b107-ciu-anchor-20260928/.worktrees/rg55-b107-full-gate-20260928,
exact tree 3a8bbe54068d46f34652b2ed52d19a7cddb53dd6.

At the 23:38:31Z progress check (2m17s after kickoff), its container
run-gate-assay-selfhosted-3634378-21136-1790638575 was up; exact readback
confirmed NanoCpus=3000000000 and CgroupParent=dev-gates.slice.
Wheel installation and successive Assay verdict-schema validation phases
were progressing. Comparable gate duration is about 19–21 minutes, so the
expected completion window is 23:55–23:57Z. Do not make routine progress
observations before that window; then read its verdict and wrapper exit marker
separately. Other agents' mutation/session-extract containers remain
untouched.

### RW-364 — 2026-09-29 00:12:06Z — remove B107 test assertions tied to wall deadlines

The registered tester-unified run at exact tree
3a8bbe54068d46f34652b2ed52d19a7cddb53dd6 ended 2026-09-28 23:50:16Z with
FAIL/COMMAND_FAILED (841.567s): 5,174 passed, 21 skipped, one failed.
`test_run_liveness_classifies_a_thread_join_hang_as_hung` expected
`CANDIDATE_HUNG` but observed `LANE_TIMEOUT` at its fixed 50s candidate
budget. The gate artifact does not retain a time-aligned candidate resource
trace, so this ruling does not claim that a specific host-pressure interval
caused this result. It does establish that the test's asserted bucket depends
on reaching a liveness threshold before an independent wall deadline; B107's
contract explicitly forbids any test verdict from depending on wall-clock
speed. The paired busy-loop integration test similarly asserts a bucket at a
fixed 35s wall budget and has the same invalid dependency.

Binding disposition: remove these two fixed-deadline end-to-end mutation
fixtures. Keep the deterministic injected-clock/process/resource monitor
tests, the real-child liveness probe with virtual time, runner exception-to-
reason mapping, mutation bucket classification, and evidence persistence.
No production policy or verdict mapping changes. Preserve the current
evidence fields required by the merged validator; the older 612843ef proposal
omitted event/output counts and is not applied wholesale. The isolated repair
worktree `rg55-assay-b107-load-independent-20260929` is based on main
3a8bbe54. Its targeted set passed 52 tests in 3.08s; `git diff --check`
passed. The registered exact-tree tester-unified gate must be rerun after this
repair and be green before B107 can be considered closed. No Assay release or
install is authorized by this ruling.

### RW-365 — 2026-09-29 00:18:53Z — detached full-gate launch attempt did not start

The first launch attempt for the repaired exact-tree tester-unified gate
reported wrapper PID 3679513 but did not survive the noninteractive shell.
At the required 90-second check its log was zero bytes, the wrapper and
run-gate processes were absent, no matching container existed, and the
worktree had no `tester-unified` history record. This is a launch failure,
not a gate verdict and not evidence that tests ran. Admission preflight at
00:14:52Z had host memory `full avg10=0.00`, the host `dev-gates.slice`
loaded with `CPUQuotaPerSecUSec=5s`, and no active tester-unified container;
the unidentified UUID container was verified as the Pterodactyl server and
left untouched. The `session-extract` container remained active and untouched.
Relaunch with `nohup … & disown`; the exact-tree gate is still required.

### RW-366 — 2026-09-29 00:21:20Z — use managed execution session after second no-start

The second repaired-tree launch used `nohup` plus `disown` and wrapper PID
3683525. At its 90-second check the retry log was again zero bytes, the
wrapper/run-gate processes were gone, no tester-unified container existed,
and no run-gate receipt was written. This is a second launch failure, not a
test result. The execution service is cleaning up shell-backgrounded
descendants even when disowned; do not spend another cycle on shell
detachment or move this gate to a host service. Run the canonical gate in a
tool-managed foreground execution session instead, retain its session handle,
and capture the exact run-gate exit status from that session. Because this
entry is documentation-only, the B107 test tree is unchanged; run the gate at
the resulting clean branch tip.

### RW-367 — 2026-09-29 00:22:30Z — defer B107 full gate behind the active Assay gate

At fresh admission preflight, host memory `full avg10=0.00`; host
`dev-gates.slice` was loaded with `CPUQuotaPerSecUSec=5s`, and the B107 repair
worktree was clean at `6725bdaa6b6e0ef465a1d742a77729dae08a975e`. A
tester-unified container owned by the concurrent Assay work was already up:
`run-gate-assay-selfhosted-3691746-2518-1790641339`. Per the one-Assay-gate-at-
a-time coordination, I did not launch a second tester-unified gate. The
existing gate and session-extract campaign were left untouched; no B107
container or verdict exists yet. Do not inspect the concurrent gate again
before 25 minutes from this preflight unless its owner sends an earlier
completion notice. During the same host-escape preflight, mdt observed missing
cgroup2 `memory_recursiveprot` and `memory_hugetlb_accounting` flags and
restored them from the existing tracked host-setup policy; no host-setup
source was edited in this session.

### RW-368 — 2026-09-29 00:28:56Z — isolated Sol review active for B107 test repair

A fresh GPT-6-Sol xhigh review is running against candidate
`ed2c4cfb4941cc5b3b68bd65ff8de298fef396ac` in separate worktree
`.worktrees/rg55-assay-b107-load-independent-sol-20260929`, branch
`review/rg55-assay-b107-load-independent-sol-20260929`. Codex session ID is
`01a0ea8e-cab4-7dd3-9963-b8e3e6f4046a`; the attached execution handle is
`38034`. Its scope is the timing-dependent test removal and B107 behavioral
coverage; it cannot start gates, mutate the host, merge, or release. The
reviewer may make scoped fixes only in that review worktree. The implementation
worktree remains unchanged after candidate `ed2c4cf`; the 52-test targeted
iteration result is green, while the exact-tree registered tester-unified
gate remains pending behind the concurrent Assay gate recorded in RW-367.

### RW-369 — 2026-09-29 00:35:37Z — Sol review retry after sandbox startup failure

The first Sol xhigh review session (`01a0ea8e-cab4-7dd3-9963-b8e3e6f4046a`)
could not run even `pwd` or `git rev-parse`: its workspace-write sandbox
failed during setup with `bwrap: Can't mount proc on /proc: Operation not
permitted`. The session made no repository changes, ran no tests, and
returned no verdict. The isolated review worktree remains clean; it was
fast-forwarded from `ed2c4cf` to this candidate `6831b8f8` to include the
latest controller record. Start a fresh caller-selected GPT-6-Sol xhigh
session with `danger-full-access` sandbox in that review worktree; keep the
same no-gates/no-host-changes scope. The external Assay tester-unified gate
from RW-367 remains untouched and must not be checked before its stated
25-minute window.

### RW-370 — 2026-09-29 00:37:09Z — fresh Sol review relaunched with working execution

Fresh caller-configured GPT-6-Sol xhigh review session
`01a0ea97-f1dc-7f22-963e-cf173f3004d0` is now active in
`.worktrees/rg55-assay-b107-load-independent-sol-20260929` at candidate
`0a6f74f3bceedde6899a4e16a6405b64d637157b`. The prior session is closed
without a verdict. This retry uses Codex `danger-full-access` because the
workspace-write bwrap could not initialize; its scope remains isolated to
review/test files and report, with gates, host changes, release, and merge
forbidden. Attached execution handle: `11710`. Do not advance or edit the
review worktree while it is active.

### RW-371 — 2026-09-29 00:43:56Z — Sol ACCEPT; B107 registered gate still pending

The fresh caller-configured Sol xhigh reviewer completed successfully and
committed an ACCEPT report at
`.worktrees/rg55-assay-b107-load-independent-sol-20260929/assay/nyxloom-trove/reports/assay-RG55-B107-LOAD-INDEPENDENT-SOL-FINAL-REVIEW-20260929.md`
(review commit `e0f9db79892b9068cea0bedf65b7e77e371b7e6c`). It found the two
removed CLI mutation assertions depended on racing fixed candidate wall
budgets against liveness thresholds; it found no other assertion with that
classification race. Its local focused checks passed (91 tests, 53 cached
evidence tests, 1 surviving CLI test, and 9 judge/verify tests). These are
reviewer-run local checks, not a registered gate receipt. The reviewed
candidate was `0a6f74f3`; an independent comparison confirmed the repair
worktree at `7ee9a8ad` has no diff in `assay/`, so the code/test delta is the
same. The reviewer worktree is clean. No fix-verification round is required.

The exact-tree registered `tester-unified` gate remains mandatory. Its
read-only dry run returned 0 and resolved the registered command to
`assay/tools/tester-unified-gate.sh` for the repair worktree. The run-gate
profile plan is enabled. The cockpit has no `/run/cgprofile/ctl.sock` and no
`cgprofile` executable in `/home/vscode/.venv`; do not set
`RUN_GATE_PROFILE=off` to conceal that state. Record the profiler's actual
graceful outcome during the gate; under R-36h it must not block or change the
functional verdict.

Process-control disclosure: the controller's general container inventory at
`00:38:58Z` was earlier than RW-367's stated 25-minute no-check window. It
showed no matching active container, but this premature observation is not
used as completion or admission evidence. Make no further check of that
concurrent Assay gate until `00:47:30Z` absent an owner notification. At or
after that time, check once; if its slot is free, repeat host-PSI and loaded
slice preflight and launch the repaired-tree registered gate in a
tool-managed foreground session, applying the 3-CPU cap to its exact printed
container name immediately after launch.

### RW-372 — 2026-09-29 00:58:40Z — preserve P1 hung outcomes as unresolved pending B107 rejudge

Read the terminal P1 R2 receipt on exact tree
`0080eba7f91128d4df2d50368b7edaf1465f7805` in the preserved isolated
checkout. It is `BUDGET_EXCEEDED/CANDIDATE_HUNG`, exit 4: 125 candidates
accounted for, 115 killed and 10 classified hung. Each of the 10 progress
records says `tests_completed=1394`; the retained candidate records do not
include a time-aligned host/cgroup resource trace. Therefore the result does
not establish whether any individual classification came from a genuine
post-test hang or scheduling/pressure, and none is relabeled or closed here.

The accepted B107 monitor requires complete clear host/candidate resource
intervals before advancing an idle window; any observed pressure, missing
source, or counter reset pauses the liveness clock and leaves a wall-budget
expiry explicitly inconclusive. Once RW-371's exact-tree full gate passes and
the test-only repair is provisionally merged, P1 must be rejudged on its
final quiet tree with the B107 monitor. Preserve that run's per-candidate
evidence and classify each terminal outcome from the actual receipt; do not
infer pressure causality from this old result.

### RW-373 — 2026-09-29 01:10:10Z — B107 exact-tree registered gate PASS

The registered `tester-unified` gate passed in CIU-managed worktree
`.worktrees/rg55-b107-ciu-root/.worktrees/rg55-b107-final-gate-20260929` at
exact tree `7dd3a8cee6504732b7f58588ccae7151f8d94ff1`. Container
`run-gate-assay-selfhosted-3730117-25109-1790642983` exited 0; the immediately
applied cap and placement read back as `NanoCpus=3000000000`,
`CgroupParent=dev-gates.slice`. The independent run-gate history read reports
PASS, `dirty=false`, `history_eligible=true`, exit 0, and 842.547 seconds
(00:49:43Z–01:03:45Z). The gate completed its wheel build/install, self-hosted
Assay lane, Topos qualification, CMRU B006(a) qualification, independent
self-hosting witness, and pyflakes phase.

R-36h disclosure: `cgprofile-host-daemon` was not running. Run-Gate emitted
its named warning and used coarse rusage sampling; the functional gate still
passed, with no profiler token/daemon measurements. `RUN_GATE_PROFILE=off`
was not set. The B107 code/test delta is unchanged between gated tree `7dd3a8ce`
and the current repair branch; later commits are controller/review report
records only. Sol's ACCEPT artifact was cherry-picked onto the repair branch
as `ad32d14e`. This evidence supports the requested provisional merge; it
does not authorize an Assay release or install.

The isolated CIU root was necessary because CIU inventory in the primary
checkout refuses on the unrelated stale P6 identity record; the P6 checkout
was left untouched. CIU marked the B107 worktree ready, while its optional
network creation warned that Docker's predefined address pools are exhausted.
The gate did not use that network (`tester-unified` is launched with network
disabled), so the warning did not affect this run. No network was removed.

### RW-374 — 2026-09-29 01:17:55Z — P1 R2 launch refused shared-clone object alternates

The first P1 R2 attempt was started in CIU worktree
`.worktrees/rg55-b107-ciu-root/.worktrees/rg55-p1-r2-20260929` at exact
commit `4e418154863ab2563a01712567bce8f0ccbace5a`, with `origin/main` updated
to that same SHA. The registered lane launched container
`run-gate-vbpub-r2-3756694-1790644522` with `--cpus 3` in `dev-gates.slice`,
then exited 2. Its separately read Assay verdict is `ERROR/GIT_FAILED`: the
isolated root had been created with `git clone --shared`, leaving
`.git/objects/info/alternates`; Assay correctly refuses a source snapshot
whose object bytes are not local. Progress contains only the run-start and
error-verdict events (`candidate_total=null`), so **zero mutation candidates
ran**. This is a failed launch, not mutation evidence. Preserve its verdict,
progress, run-gate history, and worktree unchanged.

At the launch admission check one other mutation container,
`run-gate-vbpub-mutation-3747550-1790644029`, was active and left untouched;
this attempt used the second allowed mutation slot. Host memory `full avg10`
was 0.00 and `dev-gates.slice` was loaded at 5 CPUs. `host-escape`/mdt
restored the tracked cgroup2 mount options; no host-setup source was edited.
The retry must use a fresh independent clone (no object alternates), verify
that fact before creating its CIU worktree, then repeat the normal admission
check. Do not convert or delete this failed checkout.

### RW-375 — 2026-09-29 01:36:21Z — P1 R2 ran zero mutants because the target was post-merge main

The clean independent-clone retry used CIU worktree
`.worktrees/rg55-p1-r2-clean-20260929` at `9e3429412097b30eebbb8433ac1c22ac0a566e4c`.
The registered `r2` command completed its R0 baseline (1,394 tests passed),
then wrote `INCONCLUSIVE/NO_MUTANTS`, exit 5, after 72.846 seconds. The
separate verdict and progress records report `candidate_count=0`,
`candidate_total=0`, and no mutation records; run-gate history is eligible but
is not mutation evidence. The exact container was
`run-gate-vbpub-r2-3767927-1790644806`, placed in `dev-gates.slice` at
3 CPUs; it had already exited by the 90-second health check.

Root cause: the target was the current main tip after P1 had already been
provisionally merged. Assay therefore resolved `base=4e418154` (main's
first-parent) and the P1 source changes were already in that base; the only
post-base delta was controller documentation, outside the configured
`scripts/cgroup-profiler/lib` mutation roots. This was a wrong-tree launch,
not a P1 mutation pass/fail and not evidence about host contention. Preserve
all artifacts and do not resume this zero-candidate receipt as if candidates
had run.

The correct rejudge lineage is the isolated P1 branch whose pre-P1
`origin/main` is `e5e9b95c`: its current reviewed follow-up tip includes the
accepted `proc stat` identity and explicit-null DAMON repairs (Sol round 4,
product tip `cc7e1910`). Before the next attempt, carry in the B107 Assay
source changes (`193b8dd0`, `3c63ca34`, `76325aa5`), verify the merge base
still resolves to `e5e9b95c`, and create a fresh CIU worktree with no stale
run-gate inflight state. Then repeat admission checks and run R2 on that quiet
tree. The prior 1080ac2 R2 PASS predates these P1 repairs and is not final
evidence. No candidates from this attempt are counted toward the campaign.

### RW-376 — 2026-09-29 01:49:26Z — P6 migration permission errors must not certify placement

While resuming P6, inspection of the private-PID migration path found a false
success state: non-`ESRCH` errors writing a lane PID to the gates leaf were
silently skipped. In the concrete `EPERM` path, systemd fallback was correctly
not attempted, but `enforcement_failed` stayed false, so the empty leaf could
remain and the response could say `placement.error=null`, `pids_moved=0`.

On the isolated P6 branch, non-`ESRCH` write failures now fail closed, log the
error, report the existing `place-refused:write-failed:<leaf>/cgroup.procs`
shape, and abandon an empty leaf. The regression test verifies no systemd
fallback, exact refusal, and leaf removal. The focused test passed and the
complete `test_serve_placement.py` file passed 122 tests in 17.18 seconds;
these are local iteration results, not registered gate evidence. P6 exact-tip
short gates, changed-line and branch coverage, the live private-PID start and
stop probes, a fresh Sol round-5 review, replacement R2, and the full gate
remain outstanding. The code fix has not been merged to main.

### RW-377 — 2026-09-29 01:55:48Z — launch P1 R2 on the correct pre-P1 mutation base

After RW-375, the P1 candidate was rebuilt in the independent CIU root from
the isolated lineage whose `origin/main` remains `e5e9b95c5ac8be3452c93f1066f9436347f862fd`.
The exact committed candidate is `dcdcc5d323e16da68f46874eeb34dbfaab7891bd`:
it contains the P1 reviewed code and Sol-round-4 `proc stat`/explicit-null
repairs, plus the merged B107 pressure-aware Assay source through
`76325aa5`; the source under `assay/src/assay` compares equal to current main.
The candidate has no object alternates, a clean tree, no prior R2 history, and
`git merge-base origin/main HEAD` resolves to `e5e9b95c`. The expected P1
mutation scope is therefore present again; the earlier `NO_MUTANTS` receipt
is not reused.

The registered `run-gate r2` started at `2026-09-29T01:41:52.960Z` in fresh
CIU worktree `.worktrees/rg55-p1-r2-isolated/.worktrees/rg55-p1-b107-rejudge`.
Its exact container is
`run-gate-rg55-p1-r2-isolated-r2-3806635-1790646112`, verified at
`NanoCpus=3000000000`, `CgroupParent=dev-gates.slice`. At launch host memory
PSI `full avg10=0.00`, the gates slice was loaded with a 5-CPU quota, and one
other mutation container was active; this campaign uses the second mutation
slot. At the required 90-second health check the container was still running
with the cap in place. The R0 baseline completed 1,394 tests; Assay selected
125 candidates, and candidate 0 (`access.py:48`, `Is->IsNot`) was recorded
killed. This is initial progress, not a final result. A prior 125-candidate
P1 R2 took about 1h44m; given the other active mutation campaign and shared
5-CPU gates slice, estimate roughly 1h45m–2h45m, subject to observed runtime.
No further progress inspection before 25 minutes after the 90-second check
unless an error/completion is expected; next routine observation is no earlier
than 2026-09-29 02:08Z. Keep this worktree HEAD quiet until the terminal
verdict is read separately.

### RW-378 — 2026-09-29 01:58:10Z — P6 coverage gate found the new refusal-log branch uncovered

P6's registered `run-gate r0-r1` on exact tree
`e4241e39445ce1c074568b727f0b927a5e6103c7` finished with exit 2 after
140.991s. All 1,705 tests passed, but the RG-53 branch-aware coverage judge
found one uncovered line and one partial branch in `lib/placement.py` line
599: the newly added diagnostic logger path for non-`ESRCH` migration write
errors. Project totals were 6,167/6,168 statements and 2,147/2,148 branches
(99%, below the required 100%). This is a coverage failure, not a functional
test failure. Run-gate history was read separately and confirms the exact
tree/exit. The profiler daemon was down, so the gate used coarse rusage under
R-36h; this does not affect the coverage result.

The same behavioral regression now provides a log sink and asserts the
specific PID/error diagnostic. Its full containing file passed 122 tests in
17.00s. The test change is committed in P6 at `e7bca65e`; controller log
through RW-377 was reconciled into P6 at `4af3d3c0`. R0/R1 must be rerun on
the resulting quiet tip, followed by R3, fresh Sol round 5, live probes,
replacement P6 R2, and the full gate. No P6 code has been merged to main.

### P6 short-gate evidence — 2026-09-29 02:08:07Z (package receipt)

P6 tree `d7603b5292779a227fe9177254668eb50049f147` now passes both registered
short lanes. `r0-r1` ran 02:01:27–02:03:50Z (143.567s; exit 0): all **1,705
tests passed**, with **6,167/6,167 statements and 2,148/2,148 branches**.
Run-gate history independently records `outcome=pass`, `dirty=false`, and
the exact commit. The new `lib/placement.py` diagnostic path is covered; this
closes RW-378's coverage miss. Its exact gate container was
`cgprofile-gate-3848809-1790647289`, capped at 3 CPUs in `dev-gates.slice`.

`r3` ran 02:04:36–02:04:51Z (14.549s; exit 0): **7/7 canaries rejected**
(`counter-reset-negative`, `absent-reads-as-zero`, `limits-ignore-ancestors`,
`slice-hierarchy-flattened`, `follow-children-disabled`,
`manifest-renamed-to-jsonl`, `log-timestamp-uses-arrival-time`). Its history
record matches the same exact commit; container
`run-gate-vbpub-r3-3855123-1790647476` was capped at 3 CPUs in
`dev-gates.slice`.

The daemon was down for both lanes. R0/R1 used coarse rusage; R3 used basic
container sampling, not DAMON. R3 still passed while host loadavg was 5.68 at
start and 6.04 at finish, with host CPU `some` pressure around 11%: no resource
measurement participates in the canary verdict. These results do not replace
the required live daemon/carrier/placement probes or measured DAMON series.
The receipts are on `d7603b5`; after recording them in the package report, rerun
the short lanes on the final documentation tip before Sol round 5.

### RW-379 — 2026-09-29 03:42:02Z — stall-kill requires a kernel-contained lane

Binding B1 ruling, preserving the private PID/cgroup/network namespace design
and D-15's fail-closed write boundary. A host PID read through the explicit
host-proc view is not signal authority: remove the cross-namespace `os.kill`
fallback and never certify PID-number equality as process identity.

`--on-stall kill` is accepted only where the daemon can address one exact
kernel cgroup boundary. For scope `container`, the target must resolve to the
container-ID-named cgroup beneath the verified, authored, bounded
`dev-gates.slice`; enforcement is one `cgroup.kill` write to that exact target
cgroup, leaving its Docker cgroup membership and limits intact. D-15's
whitelist is extended only for this exact target's `cgroup.kill`, with no
ancestor or sibling write authority. For scope `container-shared`, `kill`
requires a token and an explicitly requested, successfully verified
`<gates slice>/rg-<token>` placement leaf before the session is accepted; the
daemon must not move a lane into a leaf only after it stalls. The consumer
must request that leaf even when it has no memory/CPU cap to apply. A refused
or unverified containment request rejects `start` as `bad-policy` (no
session); an enforcement failure after an accepted session remains
`reported`, never `killed`.

`report` remains available for all valid scopes. The run-gate consumer keeps
its own stall enforcement/fallback, so inability to start an enforceable
daemon session is disclosed and cannot change a test verdict. Contract v1.1
§8.3–§8.4, §8.8–§8.9 and both byte-identical mirrors, P5's shared-scope
placement request, D-15 documentation/tests, and live acceptance probes must
implement this ruling before P6 review can pass. The focused live probe must
show the selected gate lane exits while a different lane and the daemon
remain alive.

### RW-380 — 2026-09-29 03:55:30Z — do not add placement solely to obtain stall kill

Refinement to RW-379 after applying the estate's performance-invariance
constraint: a liveness policy must not silently move a lane into a different
CPU/memory hierarchy just to make daemon enforcement available. For a
`container-shared` session, `--on-stall kill` still requires an explicit
`--place` request and a successfully verified leaf; no PID-signal fallback
is permitted. The run-gate consumer requests `kill` only when its already
derived placement plan requests a leaf; otherwise it requests `report` and
keeps its existing lane-local stall watchdog as verdict authority. Do not
invent a placement request when no declared or measured resource fact
supports one. A requested placement that is refused makes daemon `start`
return `bad-policy`; the consumer's profiling fallback must remain
non-blocking and preserve the lane-local verdict path. Scope `container`
continues to use the exact target-container `cgroup.kill` under the guards
from RW-379.

### RW-411 — 2026-09-30 03:31:40Z — resume checkpoint: P1 rejudge and closeout

RG-45 and RG-54 are now closed in the run-gate backlog by commit `6e1d6984`.
The entries retain their cross-project provenance and state the remaining
product boundary; this does not change the P1 judged tree.

P1's current R2 campaign is the correct isolated pre-P1-base tree
`4e5ff2d2a28d153195995df4c1e5a03a813af802`, running in
`run-gate-rg55-p1-r2-isolated-r2-2191033-1790737267` since 03:01:07Z with
3 CPUs in `dev-gates.slice`. At the 03:27:58Z observation it had accounted
for 31/125 candidates, all killed, and was still running; no verdict exists
yet. Preserve its HEAD and wait at least 25 minutes between routine progress
reads.

The fresh P1 Sol review is operating in the separate final-review worktree.
It found that omitted `ctl start` options were serialized as JSON null and
that the package contract mirror was stale; both corrections and focused
oracles are committed there as `d5076b81`. This is not a final review verdict.
If those fixes remain in the accepted product diff, the exact corrected tree
needs fresh mutation evidence; the in-flight R2 on `4e5ff2d2` does not judge
`d5076b81`.

### RW-382 — 2026-09-30 16:52:02Z — retain the no-broker trust model for RG-55

Operator decision recorded as D-32 in
`DESIGN-2026-09-12-liveness-placement-admission.md`: RG-55 keeps the direct
daemon-to-systemd bridge and does not add a host broker for this single-
operator, rootful-Docker deployment. The operator/run-gate principal has
unrestricted Docker API access, which is already host-administrator authority;
a broker cannot protect that principal from the host. The added privileged
service and protocol are not justified for this deployment's trust boundary.

This is an explicit trust-model choice, not a finding that daemon-side
allowlists contain arbitrary daemon compromise. The current daemon retains
host system-bus, writable cgroupfs, DAMON sysfs, and privileged-container
authority. Its Python request/path checks constrain normal behavior, not a
compromised process. Do not describe this as least-privilege or as
kernel-enforced containment under D-15. Keep the cockpit on the ctl protocol;
do not mount the host system bus into it. A future broker remains an option
if the caller trust boundary changes or daemon-compromise containment becomes
a requirement; that change must remove all direct daemon bypasses and mediate
DAMON authority as well as cgroup/systemd operations.

### RW-383 — 2026-09-30 16:55:49Z — refresh RG-55 release status after P1 R2 termination

The P1 R2 campaign described as running in RW-411 is terminal. The separate
Assay receipt on exact tree
`4e5ff2d2a28d153195995df4c1e5a03a813af802` records R0 PASS and R2 PASS:
125/125 candidates killed, zero survivors, budget-exceeded, crashed, or
equivalent; it ran 2026-09-30 03:01:13Z–04:40:02Z. This is useful evidence
for that exact tree, not automatically for later source changes. The fresh
P1 review follow-up `d5076b81` recorded in RW-411 is not present in this
checkout's Git object database; its code/evidence must be reconciled into the
authoritative P1 candidate. If those fixes are included, the exact resulting
tree still needs the required short gates, R2, final review disposition, and
registered full gate before release. P1 remains only provisionally merged;
cgprofile 1.0.0 is not released and the host daemon is not running.

P6 remains unmerged at branch tip `a98b443f`. Its available R2 receipt is a
FAIL on older tree `b3df5602`: 300/312 killed and 12 survived; it is not
current-tip evidence. Its round-6 live probe found direct placement under
`dev-gates.slice` refused because the loaded slice is not delegated. D-31/A3
now records the delegated per-lane scope correction and D-32 keeps it
broker-free, but the corrected scope lifecycle, exact-tip gates/R2, live
start/restore/kill probes, fresh Sol acceptance, and full gate remain to be
completed. cgprofile 1.1.0 is not released; no cgprofile release tag or
running host singleton was found.

P2/P4/P5 run-gate releases are present through 23.9.1, `run-gate` is
installed at rev 46, and the footprint manifest is tracked. Assay 7.2.0 is
installed. The SPEC-V8 D.6 note is present. RG-55 is nevertheless not closed:
the final wave report does not exist, the dstdns adoption brief still has
⟨P3⟩ placeholders, and the live carrier/DAMON-overhead closeout is absent.
No dstdns files were read or changed for this checkpoint. RG-55 worktrees
remain and must be cleaned only after preserving required evidence and
finishing the release/closeout work.

### RW-384 — 2026-09-30 17:24:23Z — apply A3 delegated-scope placement; keep host bus daemon-only

The host read-only preflight confirms systemd 257.13, loaded
`dev-gates.slice` at `/dev.slice/dev-gates.slice`, `Delegate=no`,
`CPUQuotaPerSecUSec=5s` (five CPUs), and `MemoryMax=1610612736` bytes. This
is the expected capacity boundary; do not set `Delegate=yes` on the slice.
P6 must create one transient per-lane scope beneath it with the required
controllers delegated, use the returned `ControlGroup`, and manage only the
`rg-<token>` child below that scope. The live manager and cgroup-v2 ordering
must prove creation, migration, controller enablement, stop/restore, and
failure rollback before this is releasable.

The cockpit currently has no `/run/cgprofile` mount because it has not been
rebuilt, and no host system-bus socket. A3 needs no new cockpit mount: keep
the existing host `/run/cgprofile` bind for the consumer socket; keep
`/run/dbus/system_bus_socket` mounted only into cgprofile's daemon container.
Do not alter the operator's dirty mdt template or the live `/workspaces/dstdns`
configuration. The authorized `host-escape systemctl show` preflight also ran
mdt's idempotent cgroup-mount doctor, which restored
`nsdelegate,memory_recursiveprot,memory_hugetlb_accounting`; it reported the
result explicitly. No unit or workload was changed by the preflight.

The old P6 implementation writes directly below the non-delegated slice and
is superseded by A3; its old R2 receipt is not transferable. D-32 remains in
force: this is still the no-broker design, with its existing privileged
daemon authority documented honestly. P1's current-main candidate is
`b88d4f07` (five Sol follow-up commits replayed); exact-tree gates, a fresh
Sol review, complete R2, and the registered full gate are outstanding.

### RW-385 — 2026-09-30 17:41:09Z — sample the verified lane leaf and recover placements by identity

Source inspection found that the P6 daemon moved lane PIDs into a placement
leaf but continued sampling the original container cgroup. Once migrated,
that cgroup no longer contains the lane processes; reporting its counters as
lane usage would be false attribution. On successful placement, metric reads
must use the leaf's actual path returned by systemd and validated beneath the
delegated scope. Keep the original target cgroup as the session's logical
identity and sample-map key so existing report consumers remain compatible;
the `placement.leaf` value identifies the physical measured cgroup. If
placement is refused, keep sampling the original target and disclose the
refusal; profiling failure cannot affect the test verdict (R-36h).

Restart recovery is also part of the placement safety contract, not optional
cleanup: persist write-ahead ownership state before moving processes, including
the exact scope unit/path, each PID's start-time identity and original
cgroup/unit, plus transition state. On recovery, restore only a still-matching
PID proven to remain under that exact scope; never infer ownership from a
token-named path or stop a nonempty/unverified scope. Failed verification or
restoration preserves the leaf and evidence. The mirrored v1.1 contract and
P6 implementation/tests must encode these rules before its R2/review.

### RW-386 — 2026-09-30 18:01:52Z — implement D-31; state memory-charge limits honestly

P6 placement follows the settled D-31 design: create a transient delegated
scope beneath the verified `dev-gates.slice`, take its actual `ControlGroup`
from systemd, and create the profiler-owned `rg-<token>` leaf only beneath
that scope. Never write controllers or create lane directories directly in
the systemd-owned slice. Start, stop, rollback, and restart recovery must use
the exact scope identity and prove restoration before removing it.

The host kernel's cgroup-v2 memory accounting does not transfer pre-existing
page charges when a process migrates. Keep the leaf's kernel-native counters
and pressure as the memory evidence, but label them as charges attributed to
the leaf (not process RSS or total lane memory); a leaf `memory.max` constrains
charges in that leaf and must not be described as a hard cap on all memory
already resident in the migrated processes. CPU, I/O, PID, and pressure
sampling must use the verified leaf after successful placement. This is an
explicit limitation in the design, contract, README, and consumer guide; a
later design can add pre-placement admission or a separately named
process-memory estimate without disguising the distinction.

### RW-387 — 2026-09-30 19:45:18Z — keep leaf accounting; disclose its charge semantics

The operator selects kernel-native cgroup-leaf memory accounting with an
explicit limitation, not a new approximate per-process RSS metric and not a
deferral of shared-lane placement. Describe `memory.current`, `memory.peak`,
and leaf `memory.high`/`memory.max` as applying to charges attributed to the
leaf; pre-existing page charges do not migrate with a process. Do not call
these values total RSS or claim that the leaf limit caps all resident memory.
The README, consumer guide, protocol contract, and design must agree.

The CP-11 startup-recovery repair also covers a crash before `manifest.json`
is written and a finished/aborted manifest whose placement journal is still
incomplete. Never replay a completed journal for an already-finished session;
never guess, kill, or delete on uncertain identity. Preserve the exact scope,
leaf, and journal and publish an operator-visible failure until verified
restoration succeeds. Focused server-placement tests pass (20); the complete
placement file passes (188). These are local test results, not registered
exact-tree gate or live-host acceptance evidence.

### RW-388 — 2026-09-30 20:03:48Z — preserve truthful coverage assertion; file schema gap upstream

Nyxloom 0.8.1.dev519 rejects cgroup-profiler's `coverage-floor` assertion even
though the registered coverage lane enforces a whole-project 100% line AND
branch floor. Do not rename this to `changed-line-coverage`, which would make
a false statement about the measured scope. NL-25 is filed and merged to main
as 5e040b43 (lint on that isolated Nyxloom worktree passed). Until the
vocabulary is extended, `nyxloom lint` in this package has this one known CFG1
failure; it does not replace or weaken the registered coverage gate. Keep the
limitation visible in review and final reporting.

### RW-389 — 2026-09-30 20:20:55Z — repair exact-tree R0/R1 regressions found by D-31

Registered `r0-r1` on exact tree `831a789e` ran 149.423 s and failed (exit 1)
in `cgprofile-gate-3454297-1790798830`, with `NanoCpus=3000000000` under
loaded `dev-gates.slice`. The separately read history record matches the
tree and exit; the gate suite had 1,795 passes and 16 failures. Failures were
fixture/document drift caused by D-31: the subtree `/proc/stat` fake stopped
at `ppid` after production began requiring field-22 start-time identity; the
host snapshot fake still put leaves directly under the slice; the watch
stream test asked a fake `/proc` tree to prove a real host placement; and two
docs assertions exposed a stale safety sentence wrap and an obsolete A3 link
slug. The implementation was not weakened: updated the identity fixture,
modeled scope→leaf in the host oracle, isolated the stream contract with an
explicit placed-session test double (real transaction remains covered by the
placement suite), and corrected the prose links. The affected-file suite now
passes 441 tests with 6 skipped in 72.84 s. This is local evidence only; the
exact commit changed and fresh registered R0/R1 plus R3 remain required.

### RW-390 — 2026-09-30 21:01:39Z — close the placement coverage oracle gap before gates

The D-31 transaction and recovery code had only 66% local line/branch coverage
when measured against `tests/test_serve_placement.py`. Added behavioral tests
for systemd reply parsing and scope retirement, PID identity races, each
ownership-journal boundary, migration/refusal outcomes, recovery records, and
cleanup preservation. A placement-focused run passed 424 tests in 80.00 s; the
final post-restore-exit race test passed separately. Combined diagnostic
coverage for `lib/placement.py` is 100.0%: 1,149 statements and 498 branch
arcs, with no missing statements or partial branches. This is local evidence,
not the registered exact-tree gate. Also removed a duplicate leaf-path refusal
whose rejection condition is implied by the journal and verified-scope checks
above it; no accepted placement or recovery path changes. Fresh `r0-r1`, `r3`,
and adversarial review on the resulting committed tree remain required.

### RW-391 — 2026-09-30 21:18:49Z — repair restart-recovery coverage gaps from the exact-tree gate

Registered `r0-r1` on clean exact tree
`1e0d5a2cb94fa001eb1080018bbc6983401bb7a4` completed in 135.021 s and
exited 2. Its independent `.run-gate/history.json` receipt confirms that
commit, `dirty: false`, lane `r0-r1`, and exit 2. All 2,048 tests passed;
whole-project coverage was 99%, below the mandatory 100% line-and-branch
floor. `lib/placement.py` was fully covered; the remaining misses were in
`lib/serve.py`'s startup placement-journal recovery/final-summary branches and
`lib/subtree.py`'s malformed process identity and owned-leaf accounting paths.
The daemon was absent, so the lane used coarse rusage profiling; profiling did
not alter the gate verdict.

Added behavioral tests for non-object manifests with durable placement
journals, corrupt journals on finished sessions, live-orphan restoration or
refusal summaries, malformed `/proc/<pid>/stat` identities, and continued
attribution from the verified placed leaf after the original token root exits.
The affected files now pass locally: 525 passed, 6 skipped in 40.52 s; this
is not registered exact-tree evidence. Fresh `r0-r1` and `r3` on the committed
tree are still required before the Sol final review.

### RW-392 — 2026-09-30 22:28:14Z — disposition P6 round-7 blockers; retain charge-based memory semantics

P6 round 7 (`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P6-FOLLOWUPS-REVIEW-round7.md`)
is REJECT and is the review-series cap. Do not start a round 8. B1–B3 are
implementation defects, not unsettled product questions; repair them and ask
the same Sol reviewer session for fix verification.

B1: a missing `/proc/cgroup.procs` result is not proof that a systemd scope is
gone. Add a tri-state exact-unit verifier: only systemd's explicit
`NoSuchUnit` response for the requested unit proves absence; manager/query
failures remain indeterminate. Accept systemd auto-retirement only after the
scope and leaf paths are absent and every journaled process identity is
verified at its recorded origin or proven exited/reused; otherwise preserve
recovery state. Apply the same exact-unit and path checks to restart recovery.
B2: fail placement if any requested cap readback is missing/invalid, and
verify exact leaf membership against same-start journaled processes before
reporting placement success. B3: EOF before a newline is an incomplete
JSON-lines request; close it without dispatch or response, then continue
serving subsequent valid connections.

The operator's RW-387 choice remains binding: keep cgroup-native memory
accounting, describe it as charges attributed to the leaf since placement,
and do not call it total RSS or a hard cap on pre-existing resident pages.

P1 implementation was reconciled into the P6 worktree by merge commit
`80e6d8d2b823fa5cb546883b9ba3256ba2198b70` (parents P6 review evidence tip
`d1b2963e` and P1 repair tip `e8221c6b`). This is not a merge to `main`.
The P6 fix and reconciliation tree still needs registered clean-tip gates,
same-session Sol fix verification, and the required live cleanup acceptance
probe before integration. Local focused evidence: placement/systemd and
socket regression set 443 passed; serve/summary/docs 220 passed, 6 skipped;
proc-stat/PID target tests 44 passed. `test_cgprofile.py` could not be
collected in the cockpit interpreter because optional `numpy` is absent; the
registered gate environment must supply the full report dependencies. None
of this is registered exact-tree evidence.
### RW-393 — 2026-09-30 23:26:54Z — keep CIU judging checkouts attached; resume P1/P6 closeout

The active P1 release-review candidate is branch
`rg55-p1-release-review-20260930`, based on main
`8730098d0a8205bb398e60028e799b4ef7b18835`. On its pre-checkpoint tree
`80263df66ad989e9b6f621758d7fb328d07a7a4c`, R0/R1 passed 1,391 tests with
100% line and branch coverage (5,040 statements, 1,744 branch arcs), and R3
rejected all seven canaries. Run-gate history separately records exact-tree
PASS/exit 0 for both. The handoff and evidence refresh changes the candidate
tree; rerun R0/R1, R3, and doctor on the committed checkpoint before the
fresh P1 Sol review. This is a new review cycle (round4–round6); rounds 1–3
reviewed an older tip.

The old P1 R2 PASS on `4e5ff2d2a28d153195995df4c1e5a03a813af802` is not
transferable to the current P1 candidate. No current-tree P1 R2 or full gate
is complete. Under RW-381, provisional merge can follow green short gates and
the fresh Sol acceptance, while current-tree R2 and the full gate run
asynchronously in a separate CIU-managed worktree; no release or daemon
activation until those long gates pass and any fixes are backported/rejudged.

P6 branch `rg55-followups-cgprofile-final` at `f6a36616089cd194b306af6d2e2052d12fafc951`
has exact-tree R0/R1 and R3 PASS receipts: 2,106 tests, 100% line/branch
coverage (7,233 statements, 2,648 branch arcs), and 7/7 canaries rejected.
The daemon was down during these gates. P6 round-7 blockers B1–B3 are repaired,
but same-session Sol fix verification, live delegated-scope start/stop and
restoration probes, current-tree R2, the full gate, and release work remain.
Operator-selected placement memory semantics are cgroup-leaf charge
accounting since placement, explicitly not total RSS or a total-RSS cap. D-32
keeps the direct daemon-to-systemd bridge with no host broker and documents
the daemon's actual privileged authority honestly.

CIU did not detach the managed P6 mutation checkout. Its Git reflog records a
plain checkout to `aae66356` on 2026-09-24; the checkout is currently clean and
detached while the registered branch is at `4392bece`. Read-only checks found
no active matching run-gate/Assay process or P6 mutation container, and the
CIU source contains no worktree-detach operation. RW-393 rules that
CIU-managed judging worktrees stay attached to their recorded branch. Pin the
branch at the judged commit and keep it quiet throughout run/resume; put fixes
in a separate worktree. For this stale record, restore the checkout to its
recorded branch before another CIU lifecycle operation; never edit the CIU
identity record or auto-repair this state.

### RW-394 — 2026-10-01 00:37:41Z — repair P1 round-4 summary finding

P1 round 4 (`cgprofile-P1-DAEMON-REVIEW-round4.md`) rejected tree
`148481e4af504fc416679ed2fd8dffe380709de6`. B1 is merge-blocking: normal
`ctl stop` recomputed exact percentiles and other summary metrics by retaining
and rescanning raw sample objects, contrary to contract §1.5. The P1
controller has implemented an online reducer in the isolated P1 worktree:
scalar endpoints/references, extrema, drift count, host/slice summaries, PID
union and CPU rates update at ingestion; exact nearest-rank percentiles use
order-statistic AVL multisets. `finalize()` assembles from this state and
does not retain or scan raw series. Exact rank state grows with observed
distinct values; this is not a constant-memory sketch. The 3,601-sample
mutation oracle and focused `test_summary.py` + `test_serve.py` run pass (205
passed, 1 skipped, 19.24 s). This is local focused evidence only, not a
registered gate or review acceptance.

B2 remains an evidence blocker: current host `LoadState`/`ControlGroup` for
the authored interactive and gates slices has not been established from the
cockpit through a permitted read-only path. No host namespace or host-escape
was used. The latest observed load average exceeded 8 while another project's
R2 was active, so no additional bus-query container or RG-55 gate was
launched. B3 also remains open: short-gate receipts predate the B1 repair and
must be refreshed on the final committed P1 tip. The P1 edits are still
uncommitted at this checkpoint; do not merge, release, or activate the daemon.

### RW-395 — 2026-10-01 00:44:45Z — reconcile P1 with latest main; honor PSI gate

Main advanced to `126ccc39e151e33cc7bbcaa18bf765f9c9cd7dd1` after the P1
checkpoint at `8730098d0a8205bb398e60028e799b4ef7b18835`. The only intervening
tracked change is two lines in `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`.
P1 was reconciled with `git merge --no-ff main`; merge commit
`56c617d8c7762f6fb6d7a1a2b326285f7592941c` has current main as its merge
base. The B1 implementation commit `2e130da3` is retained.

At `2026-10-01 00:41:21Z`, memory PSI was `full avg10=8.67` and load average
was 12.21; another project's R2 and a tester container were active. No new
container, bus query, or RG-55 gate was launched under that pressure. The P1
source has a small follow-up optimization in progress: each DAMON class will
share one exact order-statistic tree for p50 and p90 rather than retain two
identical trees. It is not yet tested or committed. The earlier 205-pass
focused suite preceded this optimization and the main reconciliation; no
registered short gate certifies the current tree. Re-run the focused tests,
registered R0/R1, R3, and doctor on the eventual final committed tree before
fresh round-5 Sol review. Current-tree R2/full gate and B2 remain open.

### RW-396 — 2026-10-01 00:50:29Z — checkpoint exact P1 summary candidate

The P1 follow-up deduplicating DAMON percentile state is committed as
`c81b28372c16c065e0290355b576cd2d3e85d32f`: each class now uses one exact
order-statistic multiset for both p50 and p90. The candidate's code tree is
therefore `c81b2837` on top of reconciliation merge `56c617d8`. This change
has not yet been tested; the 205-pass local result was on its parent code
tree. Run focused tests, registered R0/R1, R3 and doctor on the final
committed candidate after evidence updates and when memory PSI permits.

P1 B2 remains open. The operator has been asked for direct-host, read-only
`systemctl show` output for `dev-interactive.slice` and `dev-gates.slice`;
this does not authorize `host-escape` or any namespace join. At the last
resource observation (`00:41:21Z`) memory PSI full avg10 was 8.67; no gate or
bus-query container has been started since. The new candidate is not yet
reviewed, provisionally merged, released, or activated.

### RW-397 — 2026-10-01 00:55:53Z — record direct-host slice evidence and P1 focused test

The operator supplied output from this read-only command run directly on the
host (not through `host-escape`):
`systemctl show dev-interactive.slice dev-gates.slice --property=LoadState,ControlGroup,Delegate,CPUQuotaPerSecUSec,MemoryMax --no-pager`.
Both units report `LoadState=loaded`, `Delegate=no`, and a five-CPU quota
(`CPUQuotaPerSecUSec=5s`). `dev-interactive.slice` is at
`/dev.slice/dev-interactive.slice`, `MemoryMax=8589934592`; `dev-gates.slice`
is at `/dev.slice/dev-gates.slice`, `MemoryMax=1610612736`. This closes P1
round-4 B2's specific current-unit-state gap; preserve the values and source
in the P1 records. It does not replace the reviewer-owned daemon, placement,
or restoration probes. P6 additionally needs current `cgprofile.slice`
state, which was requested separately and is not yet in this output.

On exact P1 tree `db044d4d37f017162976c78f624885ee5f595923`, after the DAMON
rank-tree deduplication commit `c81b2837` and latest-main reconciliation,
serial focused tests passed: `test_summary.py` + `test_serve.py`, 205 passed,
1 skipped in 15.70 s. This is local focused evidence, not a registered gate.
R0/R1, R3, doctor, and fresh Sol round-5 review remain pending on the final
quiet candidate. Do not infer their outcomes from these local tests.

### RW-398 — 2026-10-01 01:00:34Z — record current `cgprofile.slice` preflight

The operator supplied the requested direct-host, read-only output for
`systemctl show dev-interactive.slice dev-gates.slice cgprofile.slice --property=LoadState,ControlGroup,Delegate,CPUQuotaPerSecUSec,MemoryMax --no-pager`.
In addition to the two units recorded in RW-397, `cgprofile.slice` is
`LoadState=loaded`, `ControlGroup=/cgprofile.slice`, `Delegate=no`,
`CPUQuotaPerSecUSec=infinity`, and `MemoryMax=1073741824`. This is current
unit-state evidence for P6 as well as the already-closed P1 B2 observable;
it does not establish delegated-scope behavior or replace live start/stop
restoration probes. The supplied values are recorded as received at this
checkpoint; no host change, namespace join, or host-escape was performed.

The same query reconfirmed `dev-interactive.slice` loaded at
`/dev.slice/dev-interactive.slice`, `Delegate=no`, `CPUQuotaPerSecUSec=5s`,
`MemoryMax=8589934592`, and `dev-gates.slice` loaded at
`/dev.slice/dev-gates.slice`, `Delegate=no`, `CPUQuotaPerSecUSec=5s`,
`MemoryMax=1610612736`.

### RW-399 — 2026-10-01 01:09:52Z — reconcile concurrent ruling-number allocations

The P1 and P6 worktrees both independently appended controller rulings after
RW-384. The P6 controller log already owns RW-385 through RW-392 (dated
2026-09-30); the P1 worktree had independently reused RW-385 through RW-390
for later checkpoints. To keep identifiers globally unique when the branch
records are integrated, preserve the P6 assignments and renumber the six P1
entries without changing their timestamps or substance: old P1 RW-385..RW-390
are now RW-393..RW-398. Cross-references in the P1 and P6 reports/handoffs
were updated. No product decision or code behavior changes.

### RW-400 — 2026-10-01 01:22:22Z — port direct oracles for P1 R2 survivors

The completed isolated R2 on exact tree
`450fe53d0baca81ec5d32432c6c47117862fa992` remains a FAIL: 121/121
accounted, 109 killed, 12 survived, no other buckets. The survivors were
genuine parser-oracle gaps in `lib/targets.py`, not accepted as equivalent.
The isolated worktree later recorded a separate PASS on `1080ac2f` (125/125
killed), but that result does not judge this P1 release candidate.

The P1 candidate already has the fail-closed parser behavior. I ported the
missing direct behavioral tests to `tests/test_targets.py`, including absent,
malformed and non-positive `NSpid`; absent, truncated, non-numeric, and
misidentified `/proc/<pid>/stat`; malformed close-delimiter placement; and
the valid zero start-time boundary at both parser and helper resolution.
Focused `tests/test_targets.py` plus `tests/test_helper_pid_target.py` passed
167 tests in 6.50 s on content committed as `01912a917d7a6f0a417550f9c4319557a4c165ed`.
This closes the known oracle gaps locally but is not replacement R2 evidence.
Fresh exact-tip registered gates, review, and a new R2/full-gate campaign
remain required; do not transfer either isolated receipt to the release tip.

### RW-401 — 2026-10-01 01:28:58Z — reconcile P1 with current main

Main advanced from `126ccc39e151e33cc7bbcaa18bf765f9c9cd7dd1` to
`2ba90c10f00c67f7786ed0f63747c284867ba0be` (21 commits, all touching only
`nyxloom/`). I merged current main into the clean P1 review branch
`rg55-p1-release-review-20260930`; merge commit
`e76657b69b39a7462988edfcdf797639f6ceb2a2` contains no P1 product-file
overlap. The reviewer’s current base is `2ba90c10...`; the previous base
`126ccc39...` is superseded. P1 gates and round 5 must judge the exact final
candidate after these evidence changes.

### RW-402 — 2026-10-01 01:28:58Z — defer new test work under memory PSI

At 01:28:37Z, memory PSI was `full avg10=5.03`, above the launch limit of
5.0; load average was 13.91. The read-only observation found the unrelated
active tester container `run-gate-vbpub-session-extract-51644-1790817645`
and R2 container `run-gate-vbpub-r2-4124646-1790814775`. No RG-55 gate or
bare pytest was launched. Preserve both exact containers; do not read their
progress before 01:53:37Z unless an earlier completion/error signal arrives.

### RW-403 — 2026-10-01 02:00:00Z — reconcile latest main and refresh P1 gate evidence

Main advanced from `2ba90c10f00c67f7786ed0f63747c284867ba0be` to
`6ee297a4cb6412b1c66250367a1eb2ecf39e9446`; the two intervening commits
modify only Nyxloom's Claude Code session adapter and its tests. The clean P1
candidate was reconciled without conflict in merge
`55d0812299e84083143613a7de1585bd2f8f7fcc`.

Registered P1 R0/R1 passed on that exact candidate: 1,421 tests,
5,140/5,140 statements, 1,780/1,780 branch arcs, exit 0 in 107.163 seconds.
Its exact test container was capped at three CPUs under `dev-gates.slice`;
the daemon was down, so profiler data used the documented coarse rusage path
under R-36h and did not change the result. The first detached launcher
vanished without creating a process/container or producing output; the
successful run used a persistent job handle. This gate result predates the
report/handoff refresh in this ruling and must be repeated on the resulting
exact candidate. The operator has explicitly authorized gate execution
regardless of memory PSI; PSI is not a gate-launch veto or verdict input.
Keep the two-mutation-lane limit, exact cgroup placement/caps, and unrelated
container protections. R3, doctor, reviewer-owned live probes, exact-tree R2,
and the registered full gate remain open.

### RW-404 — 2026-10-01 02:12:39Z — reconcile latest main before P1 review

Main advanced from `6ee297a4cb6412b1c66250367a1eb2ecf39e9446` to
`6617c44e117ee1ceab222ce4c51ff82f30f3d0d0` in two Nyxloom-only commits
(assay config and P113 report). P1 merged it without conflict in
`7194c9be7012759e2627cc80cb251485c9fa6248`; no product paths overlap.

The exact candidate immediately before this reconciliation,
`a617f87632bd35ea56595156979d041dc33a6213`, passed registered R0/R1 and R3
with 100% line/branch coverage and 7/7 canaries rejected; doctor reported
zero failures and two warnings. Those receipts do not apply to the new merge
tip. The P1 candidate must rerun R0/R1, R3, and doctor after its checkpoint
refresh before round-5 Sol review. Main's dirty operator files remain outside
the P1 worktree and untouched.

### RW-405 — 2026-10-01 02:19:55Z — reconcile latest main before P1 review

Main advanced from `6617c44e117ee1ceab222ce4c51ff82f30f3d0d0` to
`8df26ed143925c882e65a1fe673d1447055bd754`; the intervening commit changes
only Nyxloom's P113 report and Claude Code adapter test. P1 merged it without
conflict in `0485d82e475930fcbf74040a0138268b437b30b6`; no P1 product paths
overlap.

P1 R0/R1, R3, and doctor passed on the previous candidate
`bd915d7f98929ef584deb8d65c0d00a8480e4193`, but the later main merge
invalidates exact-commit receipts. Repeat the three checks on the current
checkpoint, then dispatch round 5 with `REVIEW_TARGET=P1`, the latest P1
handoff, and the exact gate-history and doctor evidence. Main's operator-owned
dirty files are outside this candidate and remain untouched.

### RW-406 — 2026-10-01 03:52:53Z — P1 review session ended without disposition

The Sol review process reached its account usage limit after committing
round 5 at `c2716520`. Round 5 is `BLOCKED`, not ACCEPT: it verifies fixes for
the failed-start DAMON leak, session-ID collisions, and the probabilistic
run-ID oracle, but its exact-tip final gates and reviewer-owned live daemon
probes were not completed. The round-5 artifact names the missing real
ephemeral/shared/concurrent-DAMON/report/helper probes and notes that the
available image predates the candidate. No merge or release follows from the
interrupted session. Treat the reviewer as gone; the final P1 pass must be a
fresh Sol review seeded with rounds 1–5 after exact-tree gates and live probes.

At 03:46Z the review-owned container `cgprofile-r5-daemon-20261001` was still
up; preserve it until its ownership/lifecycle is explicitly resolved. At
03:48:30Z a separate `tester-unified` process was running in
`.worktrees/assay-b136-b141/assay`, container
`run-gate-assay-selfhosted-333635-16826-1790826375`, log
`/tmp/assay-b136-b141-tester-unified-retry-8.log`; it is outside RG-55 and
must be left untouched. At 03:49:43Z memory PSI was
`full avg10=30.03`. RW-403's explicit authorization permits gate execution
regardless of PSI, so this reading is not a verdict input or an absolute
launch prohibition. Given the independent active gate and the reported
transient Docker-stats outage, do not overlap another Docker-backed gate until
that slot is free; continue non-container source/reconciliation work meanwhile.

### RW-407 — 2026-10-01 04:02:00Z — make the OCI release boundary explicit

Backported the CP-14 publication fix to the P1 candidate as
`5d3c108e6`. Its local Bake alias and versioned GHCR image are separate
targets; CMRU's push path selects only `cgprofile-release`. The cherry-pick
keeps P1's backlog state and CP-14 row, while leaving P6-only log/report
updates for P6's eventual integration. Regenerated the backlog index with
`nyxloom backlog index` from the cgroup-profiler project root.

The user-facing README, DESIGN-GUIDE, CONSUMERS guide, and sparse CIU override
template now distinguish local development from an exact GHCR image pin and
show the complete `ghcr.io/volkb79-2/cgprofile:1.0.0` adoption coordinates.
The production singleton is still to use the later `1.1.0` pin after P6; the
template remains local by default until that release exists. These edits
change the P1 candidate after all prior receipts, so exact-tree R0/R1, R3,
doctor, R2, final review, and full gate evidence remain outstanding.

After confirming the Sol process had exited, stopped only its exact private
container `cgprofile-r5-daemon-20261001`; Docker reported exit 0. The stopped
container is retained (not removed) for evidence and ownership history.

No new Docker-backed gate was started while the unrelated assay
`tester-unified` run was observed active at 03:48Z. Do not inspect or stop
that other worktree's gate; resume one RG-55 gate at a time after it frees the
reported Docker slot. This sequencing is due to observed Docker API
availability risk, not a PSI-based veto; RW-403's explicit gate authorization
remains in force.

### RW-408 — 2026-10-01 04:05:25Z — reconcile P1 with current main and freeze release coordinates

Merged current local `main` (`ab62f101`) into the P1 candidate as
`ce1459ad3` with `--no-ff`. The merge brings the current Nyxloom integration
evidence and reports into the candidate; no P1 source conflict occurred.
The root main checkout's operator-owned dirty `.vscode/settings.json` and
untracked mdt sysctl file remain untouched. P1's current candidate is clean
at the merge tip; no old gate receipt applies to it.

Read-only CMRU status against this P1 checkout reports no prior cgprofile tag
and first release `cgprofile-v1.0.0`. Current main's run-gate package has
`run-gate-v23.9.1`; the next version is `run-gate-v23.10.0`. The RG-55 P5
release target is therefore updated from its stale 23.9.0/rev-43 handoff to
23.10.0/current-main revision 46 plus one revision bump after integration.
No release or publication is attempted yet; exact package gates, mutation
evidence, independent review, and the main/origin publication boundary still
apply.

### RW-409 — 2026-10-01 05:11:04Z — controller resumed after P1 provisional merge

The controller resumed with local `main` at `52e4fd2584ea0266f98ab106ddf191a86df7d708`,
the `--no-ff` provisional P1 merge. The P1 reviewed branch is its first parent
tree (`415ce74a`); the merge introduces no tree delta. Root worktree changes
remain limited to operator-owned `.vscode/settings.json` and the untracked
`modern-debian-tools-python-debug/host-setup/etc/sysctl.d/` path.

At takeover, one unrelated R2 campaign was active in
`.worktrees/cli-extended-lessons` (PID 407900,
`run-gate-vbpub-r2-407900-1790831318`). It is outside RG-55 and will not be
inspected, stopped, or otherwise mutated. Memory PSI at 05:10Z was
`full avg10=4.46`; this is recorded as an observation, not a verdict input.
No new RG-55 Docker-backed job is started until slot availability and the
prior Docker-stats incident are assessed. Continue safe non-container work
in parallel, then run P1's required exact-tree R2/full-gate evidence and P6
integration in isolated, attached CIU worktrees. The provisional merge is
not a release: cgprofile 1.0.0/1.1.0 still require their specified evidence,
and run-gate 23.10.0 must be packaged from an eligible release source.

### RW-410 — 2026-10-01 05:45:47Z — disambiguate the repeated P1 checkpoint number

The reconciled controller log contained two distinct rulings labeled RW-381:
the 2026-09-29 provisional-integration policy and the 2026-09-30 P1 resume
checkpoint. Preserve RW-381 for the policy because package reports and review
handoffs cite that binding rule; relabel the P1 checkpoint RW-411 and update
its in-log references. The separate 2026-09-16 takeover label RW-59 was
already dispositioned by RW-273 and remains preserved as that historical
alias; no policy or evidence changes here.

### RW-412 — 2026-10-01 12:30:49Z — deterministic P6 integration tests; prior container absent

P6's reconciliation with local `main` `a63d8cc7` exposed five stop-test
request envelopes that did not use contract §8.1's `args` object. They now
use the shared `_wire` constructor. A sixth failure exposed a scheduler race
in the carrier fixture: its sampler barrier could run before the new session
was entered in the registry and fall through, making sample counts depend on
thread scheduling. The test now fences that initial sleep until the start
response has returned and the registry is published. The daemon sampler was
not changed. The combined focused set then passed **912 tests, 6 skipped, in
56.61 s**; this is not registered gate evidence. Contract mirrors are
byte-identical and `git diff --check` passes.

The unrelated R2 container observed in RW-409 was absent from both `docker
ps` and the process table at 12:30Z. Per RW-409, its campaign files and
verdict were not inspected; only the exact container and wrapper PID's
absence was checked. Memory PSI `full avg10=0.00` at 12:30Z. No RG-55
container-backed gate has started yet; finish and commit the P6 integration
tree, then run its exact-tree short gates and live probes before review and
provisional integration. P1 R2/full-gate evidence and package publication
remain separate release holds.

### RW-413 — 2026-10-01 12:48:54Z — add a real-socket oracle for P6 B3

Before exact P6 gates, I checked round 7's B3 verification requirement
against the current tests. The EOF branch in `lib/serve.py` was fixed, but
the regressions covered only fake connections/incomplete prefixes; the
required real AF_UNIX test for a complete newline-less state-changing request
was absent. Added and committed the test as
`9651d915234f426b4ef368b41615295ff87699ba`: it half-closes a full `stop`
request, proves no handler dispatch or response, then sends a valid `version`
request successfully. The focused real-socket test passed 1/1, and the full
socket-carrier module passed 58/58 in 3.09 s. These are local tests, not
registered gate receipts. The CIU-managed exact-tree checkout is attached,
clean, and at `9651d915`; no RG-55 container gate has started. The unrelated
assay `tester-unified` container seen at 12:35Z is outside RG-55 and was left
untouched; no later progress poll has been made.

### RW-414 — 2026-10-01 13:02:51Z — P6 R0/R1 exposed an obsolete numeric-PID oracle

P6's first registered `r0-r1` run was on exact clean candidate
`95fcbe9d0759b6c97ac92c4d1a25702745ab1b2f`. Run-gate history was read
separately: `fail`, exit 1, duration 156.446 s, 2,144 passed and one failed.
The exact coverage container `cgprofile-gate-713722-1790859427` read back
`NanoCpus=3000000000` and `CgroupParent=dev-gates.slice`; the one-second
placement probe also read back its 3-CPU cap and verified the loaded slice.
Both exact gate containers were gone after the wrapper's own cleanup. R-36h
used its declared coarse `rusage-maxrss` fallback because the daemon was
down; this did not affect the functional test verdict.

The sole failing assertion called removed `SessionServer._pid_addressable`
from the old numeric-PID kill design. Under settled D-31, enforcement uses
only `cgroup.kill` at the exact verified boundary; tests already prove the
unplaced shared-scope refusal, the placed-leaf target, and no numeric PID
signal. Removed the stale test in
`de7245aa24a7bf180aa23ea51412fe1709e27989`; the remaining watch module
passed 51/51 locally. This was a test-only correction, not a registered
green gate. Exact R0/R1, R3, doctor, live P6 scope/restore probes, and Sol
round-7 fix-verification remain pending on the resulting candidate.

### RW-415 — 2026-10-03 08:18:12Z — P1 Sol fix-verification ACCEPT; checkpoint before provisional merge

Fresh Sol xhigh post-round-6 fix-verification ACCEPTED the P1 candidate for
provisional integration under RW-381 only; this is not numbered round 7 and
does not authorize release, installation, publication, or singleton
activation. The reviewed candidate remains `8e6b2475d0c4df5c9f15d0be6c29143ea21b7adb`
(tree `20c12dbe3347d8910f77f28236667ff9437a84dc`). The original review report
and separate ACCEPT addendum are preserved under
`scripts/cgroup-profiler/nyxloom-trove/reports/` in that candidate.

The addendum closes the earlier BLOCKED report's two evidence concerns:
(1) the registered R2/full-gate argv passes request base
`e5e9b95c5ac8be3452c93f1066f9436347f862fd`; Assay validates and uses that
resolved OID for its source diff, and `git merge-base` agrees. The plan JSON
omitting a base field is an Assay observability follow-up, not a P1 blocker;
the reviewed plan found 1,161 candidates across the nine in-scope modules.
(2) the reviewer built `cgprofile:local` from this exact candidate and
confirmed its OCI revision, then obtained current-image `ctl version` and a
real 27-sample summary. The isolated daemon ran under the authored
`cgprofile.slice`; the measured workload was a separate 3-CPU container under
`dev-gates.slice`. Both used private namespaces and the workload ran alone
while measured. Exact commands, host readbacks, outputs, and the corrected
CIU identity-record description are in the addendum. Only the two uniquely
named reviewer containers were stopped and removed.

The summary probe reported DAMON unavailable (`no pids to monitor yet`, zero
DAMON samples). It is functional live-probe evidence, **not** DAMON overhead
measurement; P3 remains open. The previously recorded exact-tip R0/R1 (2,160
tests; 100% line and branch coverage), R3 (7/7 canaries rejected), and doctor
results predate this review-record checkpoint. Commit the reports and this
ruling, then rerun R0/R1, R3, and doctor on the resulting exact tip. If all
remain green, P1 meets RW-381's provisional-merge bar; current-tree R2 and the
registered full gate remain release holds to run from a separate quiet
worktree, and P3 DAMON overhead remains a wave closeout requirement.

### RW-416 — 2026-10-03 08:59:26Z — reject the merge-topology P1 R2 receipt; judge the single-parent tree

The registered R2 that ran at P1 merge commit
`1832ea8669a22c84fc8425ce1d49aef5345c545c` is **not qualifying evidence**.
Although the run-gate invocation supplied request base
`e5e9b95c5ac8be3452c93f1066f9436347f862fd`, Assay resolved
`d58b01ca7ccf864f45665e7f0bb0b97dc2d53107` as `first-parent`, so the plan
contained only five candidates and all five were killed. The required
request-base plan is 1,161 candidates across nine P1 modules. The cause is
commit topology: this merge commit has two parents, and Assay's first-parent
resolution takes precedence over the supplied request base. The short gates
on the merge tree do not repair this scope mismatch; do not count this R2
PASS toward release.

The reviewed P1 content is unchanged in single-parent candidate
`bf7dc95fb47356ef527db08077788fe22a5a3b0b` (tree
`dbd217b855c0bcc67ad3558b61b71c44ff1acb69`, parent
`8e6b2475d0c4df5c9f15d0be6c29143ea21b7adb`). A fresh plan from that exact
tree with explicit request base `e5e9b95c5ac8be3452c93f1066f9436347f862fd`
returned 1,161 candidates, two workers, and a 600-second per-candidate
ceiling. The registered composite `gate` was launched there at 08:56:12Z;
the first 90-second check found its R0/R1 container
`cgprofile-gate-657880-1791017772` active under loaded `dev-gates.slice`,
with `NanoCpus=3000000000`. Host memory PSI `full avg10` was 0.01. Historical
P1 throughput suggests roughly 4–5 hours for the mutation portion; this is
an estimate, not a deadline guarantee. The declared R2 window remains 24h;
the per-mutant ceiling's theoretical aggregate exceeds that window, so an
expiry must be reported as incomplete, never as a verdict.

The tree and explicit base must remain fixed through this run. The earlier
merge-commit verdict remains preserved for diagnosis, but its five-candidate
`.assay` records are not reused as resume evidence for this 1,161-candidate
single-parent judgment. P3's DAMON overhead measurement and P6's own
base-scoped R2 remain separate closeout work.

### RW-417 — 2026-10-03 09:12:24Z — prepare P6's exact-base clone; serialize under the present memory ceiling

Prepared an independent, non-shared full clone at
`.worktrees/rg55-p6-r2-fullclone-20261003`, branch
`rg55-p6-r2-20261003`, candidate `9e62ea661f2385bf8fa0807f3a9ab3c394a8b179`.
The clone's `origin/main` is pinned locally to pre-P6 base
`52e4fd2584ea0266f98ab106ddf191a86df7d708`; `merge-base(HEAD,
origin/main)` equals that OID. The current `assay.toml` requires an explicit
request base, so `assay plan r2 --request-base 52e4fd...` was run and
returned 978 candidates across seven modules, two workers, and 600 seconds
per candidate. Candidate project subtree
`89e21e7c4230882734412e20ba1dc993070502e7` is byte-identical to the P1
full-gate candidate's cgroup-profiler subtree. A prior P6 campaign on
`6540f877` accounted for 312 candidates in 3h33m but ended
`BUDGET_EXCEEDED/CANDIDATE_HUNG`; it is runtime data only, not verdict
evidence. Linear extrapolation suggests about 11 hours for this inventory,
with substantial uncertainty; the 24-hour lane window remains binding.

No P6 gate has been launched. The present host `dev-gates.slice` readback is
loaded with a 5-CPU quota, `MemoryHigh=1GiB`, and `MemoryMax=1.5GiB`. The
active P1 campaign already has a 3-CPU container in this shared slice. Do
not overlap P6 mutation under this memory ceiling: a shared-cgroup OOM or
candidate kill would make the run incomplete, not a verdict, and would
confound the load-independence objective. Keep the P6 clone attached and
unchanged until the P1 composite gate finishes. The earlier CIU allocation
refusal (`[S16] ready record lacks a closed runtime identity`) remains
untouched; the standalone clone uses registered run-gate without editing
that identity or changing any CIU network.

### RW-418 — 2026-10-03 15:44:04Z — continue survivor-oracle work while P1 R2 runs

P1's exact-tree composite gate remains asynchronous in
`run-gate-vbpub-r2-659999-1791017899` (candidate
`bf7dc95fb47356ef527db08077788fe22a5a3b0b`; tree
`dbd217b855c0bcc67ad3558b61b71c44ff1acb69`). The last permitted progress
read at 15:35Z recorded 648/1,161 candidates: 574 killed, 74 survived, and
no other buckets. At that observed rate, roughly 5.2 hours remained; this is
an estimate only. Do not inspect campaign progress again before 16:00Z.
Its fresh R0/R1 had passed 2,160 tests at 100% changed line and branch
coverage. No P6 mutation run is active.

Continued behavioral-oracle work in the separate CIU-managed branch
`rg55-p1-survivor-fix` (HEAD `e2454ffa3ec206d093dd4648db50b289e266e400`)
without changing the judged tree. Added a subprocess fake that reproduces
`subprocess.run(check=True)` raising on nonzero status, then used it to test
systemd lookup/property/attach/scope-create/scope-stop refusals. The four new
focused tests pass; all 125 tests in `test_placement_systemd_helpers.py`
pass. Also strengthened successful and failed descendant-migration journal
assertions; their two focused tests pass. These regression changes are
uncommitted and not yet backported or included in any gate evidence.

The separate access-survivor investigation remains open: a P1 R2 survivor
reported at `access.py:235` was killed by a manual local mutation of that
expression against the current tests. Preserve the campaign record and
resolve this test/snapshot discrepancy after the next scheduled progress read;
do not reinterpret the live campaign or mutate its candidate tree.

### RW-419 — 2026-10-03 22:35:31Z — P1 R2 near completion; main advanced during the run

The exact judged worktree remains clean at
`bf7dc95fb47356ef527db08077788fe22a5a3b0b`. At this check, its exact
container `run-gate-vbpub-r2-659999-1791017899` was still running under
`dev-gates.slice` at the required 3-CPU cap. The latest progress record was
candidate 1,148/1,161 (killed); the preceding two were also killed. Thirteen
candidates remained. At the observed ~48 seconds per candidate, estimated
completion was around 22:45Z, subject to final-gate overhead. This was one
progress check after the 16:00Z minimum interval; do not re-read before the
estimated completion window unless an expected error justifies it. No final
R2 verdict was available yet.

Since RW-418, local `main` advanced from `565f3ba8` to
`2c1c1f573366b908214b84be907f4513fd9203d9` (115 commits ahead of origin) and
is clean. The intervening commits are chiefly dstdns CIU v8 D-658..D-661
documents and backlog updates, including run-gate RG-67/78 amendments and
cgprofile CP-15/16. The exact P1 judged worktree did not move. Reconcile
these newer main commits when integrating P1; preserve their work.

The separate survivor-fix worktree remains at
`e2454ffa3ec206d093dd4648db50b289e266e400` with uncommitted oracle changes
in placement, liveness, and their tests. The new check-semantics and journal
tests were verified green before the additional edge-case edits; the latter
have not yet been run because no fresh safe PSI check was taken. No merge,
release, or P6 mutation campaign was started.

### RW-420 — 2026-10-03 23:39:11Z — P1 R2 completed FAIL; new backlog work on main

The exact P1 judged worktree remains clean at
`bf7dc95fb47356ef527db08077788fe22a5a3b0b`. The previously tracked R2
container is no longer present. Assay's verdict records R0 PASS and R2 FAIL;
the independent run-gate history records R0/R1 PASS, R3 PASS, and the full
`gate`/R2 run failing at 2026-10-03 22:45:43Z after 49,772 seconds. All 1,161
mutation candidates were accounted for: 1,036 killed, 125 survived, with no
equivalent, crashed, hung, or budget-exceeded candidates. Survivors group as
10 in `access.py`, 5 in `liveness.py`, 86 in `placement.py`, 22 in `serve.py`,
and 2 in `subtree.py`. This is a release blocker until each survivor is
behaviorally triaged or proven equivalent and any needed oracle fixes are
rejudged on a new exact tree. Do not reinterpret or mutate the completed
judged tree.

At this check, `docker ps` had no `run-gate-vbpub-*` containers and `pgrep`
found no `run-gate.py` process. The separate `rg55-p1-survivor-fix` worktree
still has uncommitted edits in placement and three test files; the latest
edge-case additions remain untested. No new campaign was launched.

Since RW-419, main advanced eight commits to `a8ee4510` (124 commits ahead of
origin) and remains clean. New work is primarily CIU v8 D-666/D-667
specification and decision updates. The run-gate backlog now includes RG-73
through RG-80, notably RG-79 (refuse fallback to main's runner) and RG-80
(daemon-wide concurrent-gate admission); RG-67(a) was withdrawn. cgprofile
CP-15/CP-16 were also recorded. These are backlog/design changes, not P1
implementation changes, and were not started as work in this check.

### RW-421 — 2026-10-04 02:19:52Z — P1 survivor-fix integration and R0/R1 coverage

Resumed P1 on the latest local main base (`70c2c3662`) in the isolated
worktree `rg55-p1-survivor-final-20261004`; the integration candidate is a
single-parent history at `b6b2dc6599d4951183f069e83eb8c6755a528abc`. The
survivor-oracle changes were replayed onto this current base without merging
the stale P1 branch wholesale. Root main remains clean and unchanged.

The exact-tree R0/R1 run at `4636a9c4` initially failed coverage despite
2,263 passing tests: only `lib/placement.py:2127` (the exact-scope process
state in the leaf-removal recovery window) was missed. The regression fake
had reported the PID at its origin before recovery enumerated the scope. The
test now derives the PID cgroup from the fake `cgroup.procs` files and proves
restore from that exact scope. Seven focused recovery cases passed; the full
P1-focused set passed 1,069 tests with six skips.

R0/R1 then passed on `b6b2dc6599d4951183f069e83eb8c6755a528abc`: 2,263
passed, four fork deprecation warnings, and 100% line and branch coverage
(7,429 statements, 2,752 branches). Runtime was 136.59 seconds. Its exact
container was `cgprofile-gate-2341635-1791080191`, capped at 3 CPUs under
`dev-gates.slice`; the ~90-second health check saw 82% progress and host
memory PSI `full avg10=0.14`. The gate's expected test-fixture ownership
warning did not fail its test. The cgprofile daemon was down, so resource
profiling used coarse rusage; this does not close the live DAMON evidence gap.

The historical P1 R2 result is still FAIL on its unchanged judged tree
`bf7dc95fb47356ef527db08077788fe22a5a3b0b` (1,036 killed, 125 survived,
1,161 accounted). Survivor triage/fixes require a new exact-tree R2 campaign;
the previous campaign is not being reinterpreted. No merge or release has
occurred. No gate is active at this ruling. Next: record the R0/R1 result on
the final controller-log commit, run R3 and doctor, obtain a fresh Sol/xhigh
adversarial review, then provisionally merge if accepted. Resume R2 from the
reviewed exact tree in a CIU-managed worktree and backport any required fixes;
daemon live probes and measured DAMON overhead remain separate P3 evidence.

### RW-422 — 2026-10-04 02:22:54Z — P1 R3 and doctor

On candidate `0aa294334da1f81af088edf0cadfe2b82f811f54`, R3 passed: all 7
canaries were rejected, 0 survived; duration 14.2 seconds. `doctor
--worktree` exited 0 with 13 checks (9 OK, 2 warnings, 2 info, 0 failures).
Warnings: the profiler daemon is down (intentional for this review/gate
phase; lanes disclosed their fallback accounting), and the linked-worktree
 host-lane Git-view diagnostic warns that a custom host harness must mount the
 common Git dir. R0/R1 has already passed on the code tip `b6b2dc65`; it will
 be rerun after this controller-log commit so the final short-gate evidence
 names the final candidate tip. Main remains clean at `70c2c3662`; no gates are
 currently active. P1 still has no merge/release, and the new R2 campaign,
 fresh Sol review, and live daemon/DAMON evidence remain outstanding.

### RW-423 — 2026-10-04 03:09:33Z — disposition of P1 supplemental Sol review

The fresh Sol/xhigh supplemental review at exact candidate
`4c44d0775e1e2e484878a54b7c418673ab656121` found no code blocker and returned
`ACCEPT-CONDITIONAL` for provisional integration. I accept it as the
independent code-review receipt for provisional integration, subject to the
review's explicit remaining release holds. This does not make the older P1 R2
result pass, close survivor disposition, authorize release, or close P3.

The condition was the reviewer invoking `host-escape -- systemctl show` even
though the review handoff prohibited joining a host namespace or substituting
that carrier. The command ran in the host PID-1 namespace. It was read-only:
no unit, host configuration, container, network, or daemon state was changed.
This is a documented procedure deviation, not evidence of compliance; the
host-escape output is excluded from the acceptance basis and this ruling is
not a precedent authorizing reviewers to use that path. The code review and
private-namespace probe evidence stand independently: the helper's
systemd-bus verifier observed the authored interactive slice, and `ctl host`
observed the bounded gates slice. The review also reports that every
reviewer-owned probe container was removed and the daemon is down.

The report's exact-candidate R0/R1 and R3 receipts are green; doctor was
recorded on the preceding documentation-only checkpoint with the same code
tree. Commit this report and ruling, then refresh the short gates and doctor
on the resulting exact tip before provisional merge. Current-tree R2 and the
registered full gate must run from a quiet CIU-managed worktree, with survivor
triage on that verdict. The DAMON live ownership/index and measured-overhead
evidence remain open P3 work.

### RW-424 — 2026-10-04 03:20:44Z — P1 exact-tip short gates refreshed

After RW-423's evidence commit, candidate `8bd5d0a8e86de48b0c262ec07fdcfbe9b652fc36`
passed the exact-tip short gates. R0/R1 (`tools/gate.sh coverage`) passed
2,263 tests at 100% line and branch coverage (7,429 statements, 2,752
branches); run-gate history is PASS/exit 0, history-eligible, duration
144.208 s, start 03:12:29Z. Its test container
`cgprofile-gate-2435328-1791083553` ran under `dev-gates.slice` with
`NanoCpus=3000000000`; the daemon was down, so its profiling report used the
declared coarse rusage fallback. R3 then passed 7/7 canaries, zero survivors,
history PASS/exit 0, history-eligible, duration 11.438 s, start 03:16:23Z;
its run-gate argv had the 3-CPU cap at container creation under
`dev-gates.slice`. `doctor --worktree` reported 13 checks: 9 OK, 2 expected
warnings (daemon intentionally down and linked-worktree host Git view), 0
failures, 0 skipped, and 2 info.

No P1 source or test file changed after the exact code reviewed by Sol; the
later commits add only the review record and controller evidence. The
conditional review is dispositioned in RW-423, and these refreshed checks
meet the agreed provisional-integration bar. P1 is ready for a serial
`--no-ff` provisional merge to local `main`. This is not a release signal:
current-tree R2, the registered full gate, survivor triage, and P3 DAMON live
ownership/index plus measured-overhead evidence remain open.

### RW-425 — 2026-10-04 04:46:51Z — CIU gate resumed; P1 R2 active

The exact P1 candidate `8bd5d0a8e86de48b0c262ec07fdcfbe9b652fc36` is being
judged from the CIU-managed worktree
`.worktrees/rg55-p1-r2-isolated/.worktrees/rg55-p1-r2-20261004`, branch
`rg55-p1-r2-20261004`. The correct package runner is `scripts/cgroup-profiler/`
and lane `r2`; the container name printed by that run is
`run-gate-rg55-p1-r2-isolated-r2-2617646-1791087745`. Do not detach or commit
in the judged worktree. P1 still requires complete R2 accounting and survivor
triage; no result is inferred from runtime alone.

The separate CIU marker-parser fix is at `06c719802ef2b5ae25830925cd2b1390e228fa77`.
Its first registered gate attempt found contradictory stale governance tests
and exited before mutation. The tests now reflect the explicit-opt-in resource
contract, and the registered lane declares `cpus = "3"`. The focused
governance file passes (221 tests). Follow-up commit
`467152fd7c7dea4b9949712b6dfb38cd6b6d05ae` is clean. The retry started
04:43:11Z as `run-gate-vbpub-ciu-2667310-1791088991`, under
`dev-gates.slice`, `NanoCpus=3000000000`. At 04:45:20Z its baseline had
completed and R2 had selected 9 candidates; outcomes remain pending. This CIU
gate is the second active mutation lane alongside P1.

Local `main` remains `f516d7e52d11a46f73dde89d2015be05b735511b` (139 commits
ahead of `origin/main`); no new main work or publication event was found in
this check. No package has been released by this continuation.

### RW-426 — 2026-10-04 04:56:29Z — P6 current-tree preparation and CIU record refusal

P6's earlier implementation is already in local `main` through the
provisional cgprofile/P5 integration; no duplicate merge is needed. The old
P6 R2 receipt at `aae66356bf3a65ef8b3ba7fa04a8042f2feee55c` is a
`BUDGET_EXCEEDED/LANE_TIMEOUT` result from 2026-09-24 (362 candidates: 312
killed, 12 survived, 38 budget-exceeded). It is not current-tree evidence.
The 973-candidate/24-hour estimate recorded in the P6 lane describes the
older `2f3689ef` tree, not current `main`, whose cgprofile sources/tests have
since changed. Do not reuse that count as the current ETA.

The existing CIU-managed checkout
`.worktrees/rg55-p6-r2-ciu` was clean and attached to its recorded branch;
its record has complete runtime identity. It was fast-forwarded on that same
branch to current `main` `f516d7e52d11a46f73dde89d2015be05b735511b`, without
detaching it or editing its CIU record. It is prepared for the current-tree
P6 plan, but no P6 gate has started. Before launch, read the exact candidate
count from that tree; then hold HEAD quiet for the full run.

`ciu worktree inspect rg55-p6-r2-ciu --json` currently refuses before
inspecting that instance because the separate older checkout
`.worktrees/rg55-p1-r2-20261003` has a ready generic-root record with
`runtime.instance_id = null` and `runtime.network = null`. No CIU identity
record was edited. This is the generic-root parser/writer mismatch being
fixed on `fix/ciu-ready-record-generic-root-20261004`; use no CIU lifecycle
command until that refusal is fixed or separately resolved. The existing P6
checkout remains attached and usable for its registered run-gate command.

### RW-427 — 2026-10-04 05:42:39Z — CIU R1-01 repair in progress; P1 R2 checkpoint

The Sol fix-verification report at `467152fd7c7dea4b9949712b6dfb38cd6b6d05ae`
closed B9 but rejected on R1-01: CIU could publish `ready` before nested-root
facts and shared `root_entries` metadata were complete, and the markerless
`ensure` path could skip a retry. CIU-126 now records the defect. The repair
keeps the instance record `allocating` through preparation, resolves roots from
the allocated checkout's exact HEAD, stores all root entries before `ready`,
and makes `ensure` retry and validate old ready records. The focused
`test_ciu_workspace_adversarial_review.py` and `test_ciu_worktree.py` run passed
213 tests. CIU SPEC, README, DESIGN-GUIDE, CONSUMERS, and backlog now describe
the readiness contract. The Sol verification artifact remains untracked; the
registered CIU gate must be rerun on the final quiet commit before asking the
same reviewer for fix verification.

P1's actual running checkout is nested at
`.worktrees/rg55-p1-r2-isolated/.worktrees/rg55-p1-r2-20261004`; the earlier
top-level progress path was stale and must not be used. At 05:40Z the exact
container `run-gate-rg55-p1-r2-isolated-r2-2617646-1791087745` was still up,
host wrapper PID `2617646`, with `NanoCpus=3000000000` under
`dev-gates.slice`. The checkout was clean at the judged tree
`8bd5d0a8e86de48b0c262ec07fdcfbe9b652fc36`; current progress was 213/1,118
mutants, all killed, no other outcome buckets. Recent rate implies a rough
5.5–8 hour remaining range; do not poll before 06:05Z.

### RW-428 — 2026-10-04 10:58:16Z — P1 R2 interrupted; CIU backlog ID reserved

At the next due checkpoint, the P1 container and wrapper were gone. Its
registered history records `aborted` / `KeyboardInterrupt` at 09:55:59Z,
not a candidate verdict. The exact judged tree remains
`8bd5d0a8e86de48b0c262ec07fdcfbe9b652fc36`, clean on
`rg55-p1-r2-20261004`; progress reached 608/1,118 with 605 killed and 3
survived, so it must resume on that same tree before disposition. Do not
promote this partial accounting to a complete mutation result. At 10:58Z host
memory PSI was `full avg10=9.45%`, above the launch gate, so no restart was
made. A separate CIU mutation lane was active at the time; it is not ours to
interrupt.

The local main tip advanced from `f516d7e5` to `041862e3` (+2 commits) in the
shared checkout. The added commits are the run-gate worktree policy merge and
its shared admission-label fixture; our CIU fix branch remains at
`467152fd` and must reconcile current main before final evidence. Two active
CIU branches already reserve backlog IDs CIU-126 (rootless-record lifecycle)
and CIU-127 (single-stack down); this branch's nested-root readiness finding
is therefore CIU-128, not CIU-126. Those worktrees were left untouched. Local
main is now 141 commits ahead of `origin/main`; the earlier push authorization
covered only 83, so publication remains unauthorized pending renewed scope.

### RW-429 — 2026-10-04 11:04:01Z — latest main reconciled; host pressure blocks execution

Main advanced again during this check to `8ef923b7` (142 commits ahead of
`origin/main`) with the RG-49 amendment to the run-gate backlog. The CIU fix
worktree's merge commit `2c5c3a39` already has that exact main commit as its
second parent, so its eventual gate will include the current integration tip;
no merge onto main was made. The new RG-49 backlog text is unrelated to the
CIU readiness patch and is not being independently edited here.

The 11:04Z memory PSI sample was `full avg10=13.99%`; all test/gate launches
remain paused. The CIU readiness implementation is still uncommitted and its
last 213-test focused pass predates the final safety/test edits. Its prior
registered lane PASS on `467152fd` does not cover this fix, and Sol's
fix-verification still REJECTS that tree on R1-01. Next required evidence is
focused tests, then the registered CIU lane on the final reconciled commit,
then fix-verification by the same Sol reviewer session.

### RW-430 — 2026-10-04 11:11:19Z — serialize shared root metadata updates

Static audit found that the CIU-128 implementation's atomic file replacement
was still an unlocked read-modify-write of the generic workspace record. A
concurrent lease/opaque-metadata update could be lost. The fix now holds the
shared library's Git-family `workspace_lock` across reads/writes of
`root_entries` and during the historical-ready verification read; a focused
regression asserts the lock spans both the record read and write and that
opaque metadata survives. The CIU allocation lock is a distinct lock, so the
new shared lock is not self-nested. `git diff --check` is clean. These edits
have not yet been tested; the memory-PSI launch gate remains in force.

### RW-431 — 2026-10-04 15:17:45Z — resume P1 R2; CIU-128 focus green

Correction to RW-430: after memory PSI fell below the launch threshold, the
CIU-128 focused tests ran on the final uncommitted implementation and passed:
214 passed across `test_ciu_workspace_adversarial_review.py` and
`test_ciu_worktree.py`. The fix worktree `fix/ciu-ready-record-generic-root-20261004`
is based on current local main `759444fc`; main remains diverged from
`origin/main` (3 ahead, 16 behind). No publication or push was made.

P1 R2 resumed from the clean, attached exact tree `8bd5d0a8` using base
`e5e9b95c`. Container `run-gate-rg55-p1-r2-isolated-r2-3714427-1791126902`
is in `dev-gates.slice` with a 3-CPU cap. Its required 90-second check found
the runner alive and baseline pytest progressing at 120 seconds; mutation
candidate judging had not started yet. The previous partial campaign accounted
for 608/1,118 candidates, so the prior observed rate implies roughly 5.5–6
hours after baseline, subject to the resumed run's actual pace. Do not poll this
campaign more often than every 25 minutes; it has no verdict yet.

### RW-433 — 2026-10-04 15:31:11Z — CIU-128 focused repair committed

The lease/root-entry shared-record serialization repair is complete on
`fix/ciu-ready-record-generic-root-20261004`, commit `b8437c482`. After the
last code and documentation edits, the focused CIU suite passed 215/215 in
27.64 seconds; `git diff --check` was clean. The commit includes the preserved
prior Sol review/fix-verification artifact. The registered CIU lane has not
been rerun on this tree. At the last container inventory (15:15Z), the
separate CIU-127 R2 and P1 R2 together occupied both mutation slots; recheck at
the next due progress checkpoint before considering CIU R2.

Current local `main` is `74d71a90`, ahead 4 / behind 18 relative to
`origin/main` (`ca663b6d`). The remote-only movement is CMRU release-test
fixture work touching CMRU and run-gate release surfaces. No merge or push was
attempted; inspect/reconcile that divergence before publication rather than
assuming the earlier push scope still describes the remote state. P1's exact
judged worktree remains clean; its next progress poll is not due until at
least 15:42Z.

### RW-432 — 2026-10-04 15:22:58Z — serialize lease and root-entry record updates

Static review of CIU-128 found a second writer to the neutral workspace
record: CIU's lease mirror used an unlocked read-modify-write. The new
root-entry lock could not protect against that writer unless it participates
in the same family lock. `_sync_shared_lease` now re-reads and writes under
that lock, with a regression that also asserts existing opaque/root-entry
metadata survives. The DESIGN-GUIDE, SPEC, and CIU-128 backlog contract now
document the shared-lock invariant. `git diff --check` is clean.

The earlier 214-test focused pass recorded in RW-431 predates this additional
lease-lock edit and is not final evidence for it. At 15:21Z host memory PSI
`full avg10` had risen to 16.35%; additional test launches remain paused until
the launch threshold is satisfied. P1 remains on its exact clean judged tree;
no progress was polled before the 25-minute interval.

### RW-434 — 2026-10-04 15:41:01Z — cgroup-profiler release boundary

The operator chose the combined release boundary: because P1 and P6 are
already present on `origin/main` and CMRU releases snapshot that ref, ship the
combined P1+P6 source as `cgroup-profiler` 1.0.0. Create a 1.1.0 only if
substantive post-1.0.0 changes genuinely warrant it; do not manufacture a
second release to preserve the now-lost historical split. This settles the
release boundary, not the pending campaign, review, gate, publication, or
daemon-running requirements.

### RW-435 — 2026-10-04 15:52:52Z — one combined current-source cgprofile R2

The active P1 request-base campaign at `8bd5d0a8` uses base
`e5e9b95c` (2026-09-25); its `assay.toml` declares `source_roots = ["lib"]`
and `base_source = "request"`. The base-to-judged-tree diff is the combined
P1/P6 implementation delta. Read-only comparison found the judged tree's
`scripts/cgroup-profiler/lib`, tests, and release/gate configuration match
current `main`/`origin/main`; the only tracked source-tree difference under
that project is its copied `run-gate.py` (rev 46 versus rev 49). The latter is
outside the mutation source roots. Current local `main` also matches
`origin/main` for the cgprofile library/tests/config paths.

Therefore, this exact R2 campaign is the mutation evidence for the combined
P1+P6 library release; do not launch a second campaign against the stale P6
candidate solely to duplicate it. This is a scope ruling, not a verdict: the
P1 campaign must finish, every survivor must be triaged, and any library fix
invalidates this applicability until the resulting exact source is rejudged.
The final registered short/full gates must still exercise the release target
with its current rev-49 runner.

### RW-436 — 2026-10-04 16:07:54Z — DAMON initial start must not commit offline

The RG-55 P3 live probe observed `kdamond_commit()` fail with `EINVAL` before
the first start. Linux's DAMON sysfs documentation shows initial attributes
written before `state=on`, and its implementation rejects `commit` unless the
kdamond is running. Fix work is isolated at
`.worktrees/rg55-damon-start-commit-fix-20261004`: initial setup now starts
directly with `state=on`; online `recommit_targets()` retains `commit`. Its
focused test file passes 83/83. The design guide and CP-17 backlog entry
record the mechanism, boundary, and pending live-sample oracle. No live
acceptance has been claimed yet.

This changes a file under P1's `source_roots = ["lib"]`, so RW-435's running
campaign remains evidence only for its exact old tree and cannot qualify the
fix tree. Do not modify the judged campaign checkout. Once that campaign
finishes, the exact final library source needs its own R2 verdict and all
release gates; keep the existing run useful for triage, not as a substitute.

### RW-437 — 2026-10-05 02:48:22Z — DRY refactor accepted for provisional integration

The isolated branch `rg55-dry-helpers-20261004` at `6733bef5ecb6ee88a093ca56b1fc2029f48d25bf`
passed the registered `r0-r1` gate (2,275 passed; 100% line and branch
coverage) and `r3` (7/7 canaries rejected). A fresh Sol/xhigh adversarial
review accepted this exact tree for provisional integration with no blockers.
The first review invocation failed in its read-only sandbox before repository
access (`bwrap` could not mount `/proc`); it is not evidence. The fresh retry
reviewed the branch and gate artifacts and accepted the provisional merge.

This ruling authorizes integrating the reviewed DRY refactor only; it does
not mark RG-55 release-ready. The live synthetic DAMON samples and measured
overhead remain absent, and the P1 R2 result (12 survivors) is from an older
source tree. Disposition of those survivors and a mutation verdict on the
final library source, plus the required package/release/daemon gates, remain
open. The separate initial-DAMON-start fix in RW-436 also needs reconciliation,
gates, review, and live validation before it can qualify the release tree.

### RW-438 — 2026-10-05 02:57:17Z — DRY merge complete; live-probe prerequisites clarified

The reviewed DRY tree was provisionally merged `--no-ff` into local `main` as
`e5d9962c25e18d443f5e3712c906fff57c0d561e`. This is not a release or a claim
that the wave is complete. The RW-436 initial-DAMON-start fix and its kernel
documentation were cherry-picked without conflict onto a fresh worktree based
on this merge; that candidate is `a3735a42e` and still needs registered gates,
fresh Sol review, and real-kernel acceptance.

Read-only host inspection found authored `dev-gates.slice` loaded at
`/dev.slice/dev-gates.slice` (5 CPUs, 1.5 GiB) and `cgprofile.slice` loaded at
`/cgprofile.slice` (1 GiB). No cgprofile daemon service/container was running;
the control socket therefore did not yet exist. The current cockpit already
bind-mounts host `/run/cgprofile` at the same path, so once the daemon creates
`ctl.sock` both socket and Docker-exec carriers can be probed; this is not a
missing-mount finding. Two tester-unified containers were already active, so
no new gate or synthetic workload was launched during this check.

Historical notes saying the broader suite “could not collect” refer to
non-authoritative pytest attempts in the cockpit interpreter, which lacked
optional report dependencies. They do not describe a tester-container failure:
the registered exact-tree `r0-r1` gate for RW-437 collected and passed 2,275
tests with full line and branch coverage. Do not run package tests in the
cockpit; use the project's registered tester-unified lanes.

### RW-439 — 2026-10-05 03:00:33Z — defer new container launches while memory PSI is over the gate

Before launching the startup-fix gate or a synthetic workload, `/proc/pressure/memory`
reported `full avg10=18.06` (CPU `full avg10=0.00`). The RG-55 launch limit is
memory `full avg10 <= 5`; therefore no new gate or workload container was
started. The fix candidate remains clean at `a3735a42e`; run its registered
gates after a later preflight meets the memory limit and existing tester
containers have released their resources. This is a resource-safety deferral,
not a gate result.

### RW-440 — 2026-10-05 03:16:15Z — repeated launch preflight remains over the memory limit

After the operator's resume instruction, the 03:14:25Z preflight still read
memory `full avg10=19.39` (CPU `full avg10=0.00`), above the RG-55 launch
ceiling. No RG-55 gate or synthetic workload was started. The only newly
visible tester container inspected belonged to `cli-extended`'s worktree, not
this wave; it was left untouched and is not RG-55 evidence. Startup-fix HEAD
`38291bb58f20f5d4dd4dbeab397ac456ef810801` is clean and passes `git diff
--check`; registered gates remain pending.

### RW-441 — 2026-10-05 03:21:48Z — operator authorizes one gate despite PSI hold

The operator explicitly directed “launch regardless” after RW-439/RW-440
recorded memory PSI above the 5% launch ceiling. Scope this override to one
bounded, non-mutation `r0-r1` gate on the current startup-fix candidate. Keep
the gate in `dev-gates.slice` with the gate's verified 3-CPU cap; this does not
authorize a mutation campaign, extra probe workload, or changes to another
agent's containers. Preserve the preflight reading (`memory full avg10=19.39`)
and disclose it with the verdict.

### RW-442 — 2026-10-05 05:40:20Z — DAMON pool lifecycle must honor global sysfs resize and verified-stop constraints

The Sol/xhigh round-2 review rejected the P1 startup repair on two lifecycle
blockers. Binding repair: reserve the daemon's configured DAMON pool capacity
before the first owned kdamond is started, because the kernel refuses
`nr_kdamonds` changes while any kdamond is running, including foreign monitors.
If a foreign monitor prevents initial reservation or the kernel permits only a
partial pool, the daemon may report DAMON unavailable for an affected start;
ordinary profiling and its verdict continue unchanged (R-36h). Once monitoring
has begun, do not grow the pool. A stop/write/readback that does not establish
`state=off` quarantines the owned slot; do not reuse it or shrink the shared
count across it. Daemon shutdown retries cleanup. Restore the baseline only
after owned slots are confirmed off, all remaining monitor states are
readable/off, and the count still matches the ownership boundary; never stop
or delete foreign slots. Optional DAMON teardown errors must be logged and
must not abort session finalization or ordinary samples.

The design rationale, README behavior summary, consumer guidance, CP-17 record,
fake sysfs model, and lifecycle tests are being updated together. This ruling
does not waive the exact-tree coverage/canary gates, same-reviewer fix
verification, current-main reconciliation, P1 mutation disposition, live DAMON
sample/overhead evidence, or release requirements. No test gate or container
has been launched for this repair as of the ruling timestamp.

### RW-443 — 2026-10-05 06:01:55Z — first post-RW-442 gate exposed stale expectations and a one-shot R-36h gap

The registered `r0-r1` run on exact tree `c8f7c70e91c5e09a539cf6270445da56d1404666`
completed with FAIL (run `2ada30366c4d4394cb79c20b12a2b59f`, 2,292 passed,
4 failed; log `/tmp/run-gate/lanes/r0-r1/2ada30366c4d4394cb79c20b12a2b59f.log`).
Two failures were stale fake-kernel expectations: a bare session cannot grow
`nr_kdamonds` while a foreign monitor is on, and foreign growth must leave the
count at 3 rather than incorrectly expecting 2. The disappeared-owned-slot
case exposed that pool bookkeeping retained live claims after an external
counter shrink; it is now reconciled into quarantine. The cleanup-finalization
test fixture lacked manifest fields required by the real manifest path and has
been completed.

The gate review also exposed a separate R-36h path: one-shot `cgprofile run`
could fail before its READY sentinel if an available DAMON interface refused
session setup, withholding the wrapped command. The current candidate catches
`DamonSessionError` both during construction and context entry, logs DAMON as
unavailable, then signals readiness and continues ordinary sampling. Regression
tests and README/design/consumer/CP-17 documentation are updated. These edits
are still uncommitted at this ruling; no gate has yet validated them. Current
memory PSI is below the launch ceiling, so the next exact-tree registered gate
is authorized by the standing RG-55 rule, not by RW-441's already-used one-run
override. The failed `c8f7c70e` gate is not coverage or release evidence for the
candidate.

### RW-444 — 2026-10-05 06:07:27Z — counter-read test fixtures must model the added reconciliation read

The registered `r0-r1` gate on `591c258d8d4d5088e427aca50b16cf73e1b9cd8e`
completed FAIL (run `64a68db76d4a888d260f8111faac5a9f`, 2,293 passed, 6
failed in 174.54 seconds; separate run-gate history records exit 1; log
`/tmp/run-gate/lanes/r0-r1/64a68db76d4a888d260f8111faac5a9f.log`). All failures
were in fake `_read_nr_kdamonds` sequences: the new acquire-time reconciliation
adds one read, shifting the tests' synthetic shrink/growth/readback events
before the intended operation. The failed assertions do not identify a new
production failure; they show the fakes no longer placed their simulated
conditions at the intended boundary. Update those sequences to target the
post-create readback or free-slot check explicitly, and make a negative
counter reading fail closed rather than classify every live slot as missing.
This failed gate is not coverage or release evidence. No next gate is launched
by this ruling; it requires a fresh PSI/container preflight after the test
fixture correction is committed.

### RW-445 — 2026-10-05 06:15:38Z — functional pass is insufficient while DAMON lifecycle coverage is below 100%

The registered `r0-r1` gate on `b30eca82100711f53ebf63839facdb4b50b4183a`
completed with 2,299 tests passing but failed its mandatory coverage check
(run `2fba4b01b6a3b08b8729711c682e2752`; history verdict FAIL, internal
`exit_code=2`, 212.496 seconds; wrapper exit 1; log
`/tmp/run-gate/lanes/r0-r1/2fba4b01b6a3b08b8729711c682e2752.log`). The package
reported 15 uncovered statements and 6 partial branches in `lib/damon.py`
(96% file line coverage; 99% total), including uncertain state/readback,
reservation, quarantine, and teardown paths. Do not treat the passing test
count as gate evidence. The candidate now adds behavioral tests for those
failure paths and makes `_reserve_capacity` receive the already-validated
baseline from `acquire`, removing its otherwise unreachable duplicate baseline
capture. A fresh exact-tree `r0-r1` run is required after commit; current PSI
has fallen below the launch threshold, but it must be checked again at launch.

### RW-446 — 2026-10-05 14:42:59Z — reconcile P1 with current main and preserve colliding backlog IDs

Main advanced from `c0d1f4410a4a10a5d9635775e43dec74c2a000fd` to
`251c3eff5fb2e5b528c9e0ae3acff59fa99c2def` (RG-84 PID/cgroup guard and
intervening estate work). Before any further exact-tree evidence, merge that
current main into the P1 candidate. The merge brings 150 paths of main-only
work into its history; under `scripts/cgroup-profiler`, main added the
cli-extended adoption item using CP-17 while the P1 branch had independently
used CP-17 for the DAMON-startup defect. Preserve both records: keep main's
adoption item at CP-17, renumber the P1 DAMON item to CP-18, and regenerate
the generated backlog index with `nyxloom backlog index`. No cgroup-profiler
implementation code changed on main. Run all final P1 gates and review on the
resolved merge tip; earlier receipts do not transfer. The separate CIU gate
was left running untouched while this source reconciliation was done.

### RW-447 — 2026-10-05 16:14:35 UTC — first final-repair R0/R1 run found six fixture failures

The registered `r0-r1` run on P1 candidate HEAD `e2e077fbd7680105ad203d3ef407371d799a21b0`
completed FAIL (run `41ed5886b1454ef6419f43b72d7a9a16`; 2,316 passed, 6
failed in 130 seconds). The two `TestStartRun` failures were fake helper
launchers whose signatures did not accept the new `damon` option. Four
`test_damon` failures were counter-read fixtures still assuming the old
sequence, before the added pre-reservation reconciliation read; the ambiguous
resize test also referenced `_write_int` on the wrong object. No runtime
product assertion failed. The fixtures are corrected in the candidate worktree;
the failed run is not coverage evidence. Re-run `r0-r1` after checking PSI and
the gate-container inventory.

### RW-448 — 2026-10-05 16:29:46 UTC — DAMON lifecycle coverage is complete; one shutdown arc remains

The next registered `r0-r1` run on the dirty P1 candidate completed with
2,343 tests passing and FAIL on coverage only (run
`fe8e9f04513456b1d8848370ff8d4b98`, 132.96 seconds; history records
`exit_code=2`; log
`/tmp/run-gate/lanes/r0-r1/fe8e9f04513456b1d8848370ff8d4b98.log`). The newly
changed `lib/damon.py` reached 100% line and branch coverage. The sole
remaining gap was the false arc at `lib/serve.py:2665`, where a clean daemon
shutdown has already confirmed that the DAMON pool closed and therefore does
not emit a quarantine warning. A clean-success shutdown regression test has
been added; the gate must pass anew before this candidate has R0/R1 evidence.

### RW-449 — 2026-10-05 16:34:17 UTC — full package coverage restored on the dirty P1 candidate

After adding a direct clean-shutdown assertion, registered `r0-r1` passed
(run `97446e632ab9c9b1d6d7d6d44ef74047`, 2,344 tests, 100% line and branch
coverage across all modules, 132.97 seconds; history verdict PASS, wrapper
exit 0; log `/tmp/run-gate/lanes/r0-r1/97446e632ab9c9b1d6d7d6d44ef74047.log`).
The judged checkout was dirty, so the run is not commit-bound evidence and
did not enter eligible history. Commit the reviewed source/tests/docs, merge
current main, and rerun gates on the final clean merge tip.

### RW-450 — 2026-10-05 17:01:20 UTC — repair round-4 DAMON lock and release-boundary blockers

Sol's P1 round-4 review rejected candidate `afc32966b9fc2723e82e582c4fd45f1225283f26`
for two blockers: a non-root caller attempted `fchown`/`fchmod` on the
already-correct root-created `/run/cgprofile/damon.lock`, preventing helper
launch; and README/CONSUMERS/DESIGN-GUIDE described an unchosen separate
1.1.0 release despite RW-434 settling the combined P1+P6 tree as the first
1.0.0. The candidate now skips owner-only mutations when the lock's group and
mode already match, degrades helper-mode DAMON to off if optional lock
preparation fails, and tests that `cgprofile run` still executes its wrapped
command and preserves its exit status. It also closes the one-shot DAMON
identity fd at teardown and aligns all three human-facing docs plus the
historical CHANGES note to RW-434. These edits are not yet gated or reviewed;
the round-4 report remains preserved in the candidate worktree. At this
ruling's preflight, memory PSI full avg10 was 0.00 and two pre-existing tester
containers were running; they were inspected only and left untouched. Run the
registered package gates on a committed exact tip, then ask the same Sol
reviewer for fix verification.

### RW-451 — 2026-10-05 17:14:08 UTC — lock/deployment docs fix passes exact-tree R0/R1

The first detached wrapper attempt for `r0-r1` exited without a log, gate
container, or verdict; it did not run tests and is not a failure. The lane was
restarted as a tracked foreground command on commit
`d8056454e1723b948a2591a5ac4ae3ce70c1c8ea`. At +90s the exact 3-CPU
`cgprofile-gate-1866324-1791220221` was running in `dev-gates.slice`; pytest
was at 79% and memory PSI full avg10 was 0.00. It completed in 132.44s with
2,348 tests passing and 100% line and branch coverage for every module. The
separate run-gate history verdict is PASS, exit 0, `dirty:false`,
`history_eligible:true`, run `bc0c3e6c390907394d1735190e150cb1`. Because this
ruling changes the controller log in the judged worktree, that commit-bound
receipt no longer qualifies the new tree; after committing this ruling, rerun
the required short gates and then request Sol fix verification. R3 is still
pending.

### RW-452 — 2026-10-05 17:19:33 UTC — post-record R0/R1 and R3 pass

After RW-451 was committed, the resulting clean candidate tip
`332aeec196d23bcf7e5dab2641b81382f4aa40c3` passed the registered short gates:
`r0-r1` run `0487492e11fa801cc918a48200a2cd1f` (2,348 tests, 100% lines and
branches, 128.22 seconds) and `r3` run `188612adefc0c9253f84e3cb31b2de1b`
(7/7 canaries rejected, 10.705 seconds). Separate run-gate history records
both PASS, exit 0, `dirty:false`, and `history_eligible:true` on that same
commit. This ruling itself changes the worktree tree, so rerun both short
gates on its committed successor before requesting the same Sol reviewer for
fix verification. The previously observed no-daemon warning means the runner
used its registered coarse rusage profile; it does not affect the test or
canary verdicts. No gate containers remain active.

### RW-453 — 2026-10-05 17:44:42Z — Sol accepts P1 DAMON lock repair for provisional integration

Fresh Sol/xhigh fix-verification round 5 accepted P1 candidate
`ceb56b769ec74e7e51033876e2e31aee5c979ba0` for provisional integration.
Round-4 blockers R4-1 (already-correct root-created DAMON lock rejected by
non-root permission repair) and R4-2 (docs contradicting RW-434's combined
first `1.0.0` release) are resolved. The preserved review is
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAMON-START-FIX-SOL-REVIEW-round5-20261005.md`.
It records two low, nonblocking test-oracle limitations (fallback helper
test does not require samples/DONE; descriptor-close fake does not assert the
close call), and explicitly does not certify R2, the full gate, live helper
completion, live DAMON samples/overhead, release, or deployment.

The reviewer verified exact-tip `r0-r1` and `r3` history/log evidence on
`ceb56b7`: 2,348 passed with 100% package line/branch coverage; 7/7 canaries
rejected; both clean, history-eligible PASS. The operator supplied two
scenario-specific live lock-permission probes (daemon-first already-correct
root:gid lock without owner mutation; caller-first lock creation and group/mode
repair). The reviewer records these as operator-reported corroboration, not
independently verified live DAMON acceptance. At 17:44:26Z memory PSI
`full avg10=0.00`; no mutation/gate process was running, and existing
non-RG55 containers were left untouched.

After preserving the report and this ruling, rerun short gates on the new
commit-bound tree before provisional `--no-ff` integration. Then launch P1 R2
from a quiet, supported CIU worktree and continue the remaining P3 live
measurement/release work. This ACCEPT is not a release verdict.

### RW-454 — 2026-10-05 17:51:43Z — detached short-gate wrapper produced no job or verdict

The attempted post-RW-453 `r0-r1` launch at 17:48:58Z used a detached
`nohup ... & disown` wrapper (PID 1909488). At the mandatory +90-second
check, there was no surviving PID/process, no `cgprofile-gate-*` container,
an empty output log with no exit marker, and no run-gate history verdict.
This wrapper did not run a test and is not a gate failure or evidence. Do not
infer its status from the wrapper PID or retry it detached. Restart the
registered lane in a tracked foreground exec session; keep its exact session
handle, verify the gate container/cap/progress at +90 seconds, and read the
run-gate verdict/history separately after completion. Since this ruling
changes the judged tree, those gates qualify only the resulting commit.

### RW-455 — 2026-10-05 18:29:03Z — resume P1 and launch combined-tree P6 R2

Main remains clean at `33cfb15085cd259f2811731c377ec3879458f038`, ahead of
`origin/main` by 24 commits. P1 R2 is still running in its isolated CIU
worktree, container `run-gate-vbpub-r2-1926195-1791223388` (3 CPUs under
`dev-gates.slice`). Its last progress read, at 18:16:38Z, had judged 21/114
candidates, all killed; a 18:23Z container check still found it running.
No P1 progress was read after that check.

P6's old CIU worktree branch was a clean ancestor of current `main`, so its
tip did not represent a distinct candidate. Assay intentionally resolves a
merge `HEAD` to its first parent; planning at the current main merge therefore
selected only the P1 merge payload (114 candidates). To judge the accumulated
P6+P1 source delta without changing its tree, the P6 CIU worktree was
temporarily detached at one-parent commit `324eac950bf0b261d51d064d8a8152e3af0dbe31`.
Its tree `980863d4e3395d1d538a5cfb874af2e60a9c14aa` is byte-identical to
current main's merge tree, and `merge-base(db29266, 324eac9)` resolves to
`db29266`. The exact `assay plan r2 --request-base db29266` reported 1,251
candidates across 13 `lib/*.py` files, two workers, below the 1,500 cap.
Its printed 104-hour estimate is derived from the 600-second per-candidate
budget, not observed throughput; the P1 live rate suggests roughly 11 hours,
within the declared 24-hour lane budget.

The second allowed mutation slot launched at 18:25:04Z as registered R2,
container `run-gate-vbpub-r2-1957326-1791224704`, capped at 3 CPUs under
`dev-gates.slice`. At the startup check, the 154-second baseline had passed,
all 1,251 candidates were pending, and 8 prior records were rejected rather
than reused (`resumed_total=0`, `rejudged_total=0`). Host memory PSI full
`avg10` was 0.02. The profiler daemon was down, so run-gate warned that it
was using coarse in-lane sampling; this is not a test verdict effect. Keep
the judged P6 tree quiet and restore its recorded CIU branch before any CIU
lifecycle command after this campaign.

### RW-456 — 2026-10-05 18:43:55Z — validate cgprofile CIU deployment without starting it

The first root-level `ciu check` refused because this nested standalone CIU
root had no generated identity. `ciu env generate --root-folder
scripts/cgroup-profiler` completed; CIU warned that `ciu.env` is a legacy
write-only export (new identity facts are in `ciu.instance.generated.toml`),
created network `cgroup-profiler-cmqemn-5kibda-network`, and connected the
current `dstdns-devcontainer-vb` to it. This was CIU's normal bootstrap side
effect; read-only `docker network inspect` showed `Internal=false` and that
container as its only member. This changed the running devcontainer's network
membership; no command was issued against the `/workspaces/dstdns` checkout,
and I did not disconnect the container. The generated files are ignored by
Git and retained for the planned singleton deploy.

Root-level `ciu check` then passed but rendered zero stack configs; the
standalone service must be selected with `--dir .`. `ciu up --dir .
--render-toml` rendered its config, and `ciu up --dir . --dry-run` validated
the merged service and compose without starting Docker Compose. No volume
directories were needed. The rendered service is privileged, private-cgroup
(and no host PID setting), network-none, under `cgprofile.slice`; it has
read-only host `/proc`, writable cgroup-v2, read-only system-bus, and DAMON
sysfs mounts. Governance resolved `cgroup_parent=cgprofile.slice` and
injected no service fields. No daemon was started. The local `cgprofile:local`
image is from 2026-10-04 and predates the latest P1 repair; rebuild only after
the active mutation suites finish.

Read-only host inspection reported `cgprofile.slice` loaded at
`/cgprofile.slice`, `Delegate=no`, unlimited CPU quota, and 1 GiB `MemoryMax`.
The `host-escape` wrapper's automatic mdt doctor found missing cgroup2 mount
flags and restored `nsdelegate`, `memory_recursiveprot`, and
`memory_hugetlb_accounting`. The existing mdt host-setup defaults already
declare `CGROUP2_FLAGS=fix`; no template change is indicated. CMRU's current
syntax is positional (`cmru status cgroup-profiler --config
cmru.orchestration.toml`), not the historical `--project` form. Status shows
no `cgprofile-v*` tag; the config's first release remains the explicitly
chosen `1.0.0`, not its default `0.1.0` suggestion. No release or daemon
activation has occurred.

### RW-457 — 2026-10-05 22:03:18 UTC — P1 R2 survivor triage and oracle repair

The exact P1 R2 on commit `33cfb15085cd259f2811731c377ec3879458f038`
(tree `980863d4e3395d1d538a5cfb874af2e60a9c14aa`) ended at
`2026-10-05T19:33:13.152801Z`: 114 candidates, 89 killed, 25 survived,
zero equivalent/budget-exceeded/crashed/hung; R0 PASS and R2
`FAIL/MUTANTS_SURVIVED`, exit 1. The exact judged worktree and its Assay
records remain preserved and untouched.

The survivor table in the P1 daemon report
(`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REPORT.md`)
dispositions all 25: 19 behavioral oracle gaps now have focused assertions,
while six are justified equivalents
under the reachable-state guards and the kernel's remove/recreate behavior
for `nr_kdamonds` writes. Assay itself does not label these equivalent, so
the original verdict remains mechanically FAIL; no claim of a green final
R2 is made. The successor candidate is based on current main
`67c4c27328a76ded1cb0b8ac192382386d65e796`, whose P1 target files were
byte-identical to the old judged tree before the new oracles. Its ownership
invariant parametrization was tightened after the initial short-gate runs to
test membership and identity independently; those earlier results are
non-transferable.

The preliminary registered `r0-r1` run passed 2,366 tests with 100% line
and branch coverage; preliminary `r3` rejected 7/7 canaries. Both preceded
that final test-only adjustment. Re-run `r0-r1`, `r3`, doctor, and the full
gate on the exact committed successor. The next exact-tree R2 must follow
the final committed tests/report, with its verdict read separately. P6 R2
continues in its separate quiet tree; do not edit or inspect that tree before
the next 25-minute progress interval.

### RW-458 — 2026-10-05 23:24:20 UTC — Sol supplemental review finds bounded P1 corrections

A fresh Sol/xhigh supplemental review of P1 candidate
`c24b0d2b882df96ee6549cf357a713943398a564` returned
`ACCEPT-CONDITIONAL` for provisional integration, not release approval. The
reviewer independently checked the exact 25-survivor table against the old
R2 records (114 candidates, 89 killed, 25 survived) and the six-file direct
delta from `67c4c27328a76ded1cb0b8ac192382386d65e796`; no tests, gates,
containers, or live probes were run in review.

Two corrections were required before the review could support provisional
merge. First, `ffbf084aaf44dc23` (`damon.py:495`, `True` to `False`) was
incorrectly described as a behavioral gap because its test asserted only the
private `_foreign_growth` flag. Under the supported DAMON sysfs count-write
behavior, every indexed object is replaced; identity reconciliation already
quarantines the changed slots and blocks reuse/shrink. Reclassify this as a
sixth contract-equivalent and retain only the observable refusal,
quarantine, and foreign-marker-preservation assertions: 19 behavioral gaps,
six human-reviewed equivalents. The raw Assay verdict remains FAIL until
its real survivors are dispositioned; prose does not relabel the lane.

Second, `_close_damon_session()` treated missing `cleanup_confirmed` as
unconfirmed but logged that the slot “will not be reused” even when no
matching slot appeared in the pool quarantine. The repair now says no
no-reuse guarantee is established unless the session's identified index is
actually present in `quarantined_indices`; tests cover missing identity,
matching quarantine, and an unrelated quarantine. The override-lock oracle
was also changed from a helper-call spy to file mode/group observations, with
a separate default-path repair test. The seven focused changed-behavior tests
passed in 1.95 seconds. Repairs are isolated at
`.worktrees/rg55-p1-review-repairs-20261005`; the judged `c24b0d2b` worktree
was not modified. The same Sol reviewer was asked to retain context for
fix-verification. Exact gates and doctor on the repair tree remain pending.

Main advanced to `faa812f169a540d4ac206d1440751d12ef28be60` while the P1
candidate still forks at `67c4c273`; reconcile this movement before
integration. `/run/cgprofile` is mounted in the current devcontainer, but
`/run/cgprofile/ctl.sock` is absent, so socket-carrier acceptance remains
unavailable and no live daemon probe was launched.

### RW-459 — 2026-10-05 23:24:20 UTC — keep exact P1 R2 running; record startup and P6 pace

The first detached P1 launch attempt used the monorepo root as cwd and left
an empty log, no process/container, and no run-gate history entry; it was not
a test result. The corrected registered launch started at 23:09:35 UTC from
the P1 package directory against exact HEAD
`c24b0d2b882df96ee6549cf357a713943398a564`, base
`1882887511202d7e39f72599fcaa9fcc5e55b466`, in container
`run-gate-vbpub-r2-2803529-1791241775`. Docker inspection verified
`NanoCpus=3000000000` and `CgroupParent=dev-gates.slice`. At the required
23:11:35 UTC health check it was alive in the baseline command with no
candidate yet judged; the previous exact P1 R2 took about 90 minutes for
114 candidates, so current ETA is provisionally 1.5–2 hours, to be revised
from the next scheduled progress sample. The campaign tree remains clean and
must not be changed; this c24 campaign is useful survivor triage but cannot
certify the separate repair tree.

The separate P6 R2 progress read at 23:11:48 UTC showed 517/1,251 candidates
judged. From the 432-candidate sample at 22:16Z this is about 1.55
candidates/minute; roughly 7h50m remained at that observed rate. No verdict
was read, and no P6 tree/container was changed. Next progress inspection for
both campaigns is not before 23:36Z unless a concrete error/completion signal
arrives sooner.

### RW-460 — 2026-10-05 23:26:40 UTC — Sol fix-verification accepts P1 for provisional integration

The same fresh Sol reviewer verified the corrections on the reconciled P1
repair tree `99cffbe09d9fe72d45ee34ad61b4c767b950a1c1` and returned `ACCEPT`
for provisional integration, not release. It confirmed the 19-gap/six-
equivalent survivor disposition; the cleanup diagnostic withholds a no-reuse
claim unless the identified index is actually quarantined; and the lock tests
assert file mode/group behavior rather than a helper call. No new blocker was
found. The reviewer did not rerun tests or doctor; its full disposition and
evidence limits are recorded in
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-R2-SURVIVOR-SOL-REVIEW-20261005.md`.

The controller's seven focused regression cases passed (1.95 seconds) and
`run-gate.py doctor` on `99cffbe` reported 9 OK, 2 warnings, 0 failures, and
2 info. Both were before the review-record commit; exact-tip `r0-r1`, `r3`,
and doctor will be refreshed after that commit. The c24 P1 R2 campaign remains
active only as old-tree triage and cannot certify the repaired/reconciled
tree. Do not change its judged checkout.

### RW-461 — 2026-10-05 23:47:58 UTC — P1 provisionally merged; exact short gates pass

P1 repair candidate `ca352d8cdcda1c8014d0f782624bfc5be079192f` passed the
registered `r0-r1` lane (run `0e08e959e351ba0220ff3192a5c45dac`, 2,369 tests,
100% line and branch coverage, 199.96 s) and `r3` (run
`17aaedfe4b71cc68dab204606fb21c0a`, seven canaries rejected, zero survived,
14.113 s). Separate run-gate history records both PASS, exit 0, `dirty:false`,
and `history_eligible:true` on the same commit. Doctor on that candidate had
zero failures (9 OK, 2 warnings, 2 info); the warnings are linked-worktree
host-lane visibility and the intentionally stopped profiler daemon. With the
same Sol reviewer accepting the repairs for provisional integration, the
controller merged it `--no-ff` as `664663a52afa2fa444cee046640cc8b33296f6bb`.
The merge tree `98b0605ac9a5d813e78cbfee74f39fc3428c27b0` is byte-identical
to the reviewed/gated candidate tree. Main was clean before merge; the merge
contains only the P1 repair/tests/reports and this controller log.

This is provisional integration only. P1's active R2 on predecessor tree
`c24b0d2b` is diagnostic, not final evidence; at 23:36:25Z it had judged
45/114 candidates. P6's R2 remains active on tree `324eac95`; at 23:36:16Z it
had judged 547/1,251. Both containers remain separately isolated and capped
at three CPUs in `dev-gates.slice`. The next progress sample is not before
00:01:25Z (P1) / 00:01:16Z (P6). After a slot opens, plan one fresh combined
P1+P6 R2 from explicit request base `db29266f8a006b22a30609a74de7645d1e4c50b7`
on the final quiet tree; the existing P6 request covers the package source
set, so a duplicate P1-only campaign is not planned. Verify the plan against
the merged tree before launch.

Release remains blocked on acceptable exact-tree R2 disposition, the
registered full gate, live daemon/carrier/placement/restoration probes, and
measured DAMON overhead. The run-gate RG-55 feature surface is already in
published tag `run-gate-v23.9.1` (its wheel is present locally); the cockpit
currently has a newer development build (`23.9.2.dev1126+g998a43552`, rev
46), so no redundant 23.10.0 release is inferred from this wave. Reconcile
the installed release wheel at closeout. cgroup-profiler has no release tag;
the agreed combined P1+P6 first release remains `1.0.0`.

### RW-462 — 2026-10-05 23:49:40 UTC — full combined R2 plan validated; wait for a mutation slot

Using the exact `bec813081d84972bbd15b5eb525e438b063eff4c` tree and this
worktree's source-backed Assay 7.2.0, `assay plan r2 --request-base
db29266f8a006b22a30609a74de7645d1e4c50b7` returned `status: ok` with 1,253
candidates across all 13 changed `lib/*.py` modules, four configured
operators, two workers, and a 1,500-candidate cap. The P1 repair adds two
candidates versus the older P6 plan's 1,251; this combined plan covers the
P1 `damon.py`/`serve.py` source delta and its new oracles. The printed
375,900-second wall estimate is the 600-second-per-candidate budget divided
between workers, not measured runtime. The active P6 campaign's observed
pace (547/1,251 at 23:36Z; last interval 30 candidates/25 minutes) suggests
a much lower but variable empirical ETA; re-estimate from the next scheduled
sample rather than treating the plan estimate as a forecast.

No mutant was executed by this planning command. Both mutation slots remain
occupied by the diagnostic P1 predecessor and the older P6 tree. Once one
finishes, re-run the plan against the then-final quiet tree and start one
combined R2 from explicit base `db29266…`; keep that worktree unchanged
until completion. The current-main tree changed only for controller records
after the P1 short-gate receipts, so refresh short gates after the slot
becomes free and before launching the combined campaign.

### RW-463 — 2026-10-06 00:25:05 UTC — accept P1 socket-readiness test fix; preserve R3 PSI miss

The same Sol reviewer accepted the scoped test-only fix on exact commit
`1653143f752dfc612f6154514ee0ed85b3f35c7a` (tree
`adeb558cf589dec98838422b2a6b708ce38b6ffd`). The failing test used socket
pathname existence as readiness even though `_bind()` creates the pathname
before `listen()` returns. It now uses `_start_ready_socket_server()`, which
signals only after bind/listen completes. The behavioral assertions and
shutdown path remain intact; production code is unchanged. The fix-verification
record is
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-SOCKET-READINESS-FIX-VERIFY-SOL-20261006.md`.

The controller verified R0/R1 PASS on this exact commit (2,369 tests, 100%
line/branch coverage; run `0f6746b7f6127a1bc16a593bd0143513`) and doctor
(9 OK, 2 expected warnings, 0 failures, 2 info). R3 functionally passed with
7/7 canaries rejected (run `6ea9635d8cd0a1de9c5a28cdfd2119fa`), but its
run-gate launch sample was memory-full PSI `avg10=5.11%`, above the 5% launch
threshold, despite the preflight reading of 3.80%. The run is recorded, but
the launch-policy miss means R3 is not fully admissible evidence; rerun R3
after this record commit on the final candidate when its fresh launch sample
is at or below 5%. The reviewer did not independently verify these receipts.

The diagnostic P1 R2 on exact tree `c24b0d2b882df96ee6549cf357a713943398a564`
ended `2026-10-06T00:18:03Z`: 114/114 candidates, 109 killed, 5 survived,
zero equivalent, budget-exceeded, crashed, or hung; verdict
`FAIL/MUTANTS_SURVIVED`, exit 1. The separate clean/history-eligible run-gate
record reports 4,109.126 seconds and request base
`1882887511202d7e39f72599fcaa9fcc5e55b466`. All five survivor IDs match the
P1 report's already-reviewed contract-equivalent table; this is diagnostic
evidence only, not the final combined P1/P6 R2. The exact final combined-tree
campaign and registered full gate remain required.

### RW-464 — 2026-10-06 00:18:56 UTC — footprint has no current eligible profile history

Read-only `run-gate footprint --json` on main commit
`ac335808fb86c43644093a76c854c3032906ac5a` returned an empty `lanes` object.
The tracked `run-gate-project/run-gate.footprint.json` still has
`distilled_at=2026-09-12T21:01:24Z`; current main history has no completed,
eligible profiled PASS lane (the latest R2 entry is aborted because the
container could not reach Docker, and the selftest entry is dirty/unprofiled).
No footprint write was attempted. After the released daemon is up, obtain at
least three completed, profiled PASS runs in the project store, then run
`footprint --write` and verify the tracked manifest and `footprint --json`.

### RW-465 — 2026-10-06 21:32:07 UTC — P6 R2 terminal result and selective-reuse recovery

The completed P6 R2 on commit `324eac950bf0b261d51d064d8a8152e3af0dbe31`
is a history-eligible `FAIL/MUTANTS_SURVIVED`, exit 1: all 1,251 candidates
were accounted for, with 1,210 killed and 41 survived; there were no
equivalent, crashed, budget-exceeded, or hung candidates. The authoritative
Assay v13 verdict and progress stream remain in
`.worktrees/rg55-p6-r2-ciu/scripts/cgroup-profiler/.assay/`. Of the 1,210
kills, 1,203 have call-phase failure witnesses and seven do not. The 41
survivor IDs and mutation locations are in the verdict; candidate
`45ca2247d28b5515ca007923efb2406f9c1b66f0792aeb2b62e6b8c9794ab131`
(`placement.py:1882`) is the subject of a new test-only oracle commit.

The first selective-rejudge worker did not invoke run-gate or start a
container: it exited at `2026-10-06T18:33:54Z` with `bash: line 30: $1:
unbound variable` (log `/tmp/rg55-p6-selective-r2-20261006-watch.log`). Treat
this as a controller launch failure, not a mutation result. Its attempted
`--resume --rejudge` route also needed correction: the new test tree has a new
Assay judge identity, so ordinary per-tree resume records cannot be carried
forward.

The supported changed-tree route is `--reuse-from` the complete v13 verdict.
On isolated diagnostic branch `rg55-p6-selective-reuse-20261006`, commits
`6ebb9c13a` and `69f3fe673` opt the diagnostic command lane into run-gate
argument forwarding and explicitly declare empty pytest `addopts`; they do
not change cgprofile runtime code and are not merged into the package branch.
On the clean exact diagnostic tree `69f3fe673534e45488bade0a1bf90bf861446a93`
(tree `06e942f37b7f90015db8b5a24333e528cce9096c`), `assay plan r2` against
request base `db29266f8a006b22a30609a74de7645d1e4c50b7` verified the same
1,251 candidate IDs and the prior v13 artifact (`ddb4e19f…`). It classifies
1,203 candidates for witness replay on the current suite and 48 for full-suite
execution (the 41 prior survivors plus seven prior kills without witnesses).
The target survivor is classified for a full run. This is a diagnostic
campaign plan, not an acceptance R2 receipt; the final integrated P1/P6 tree
still needs its own complete R2 and full gate.

The run-gate dry run verified the explicit base, argument forwarding,
`dev-gates.slice`, and the 3-CPU cap, and started no container. At the
`2026-10-06T21:31Z` launch check, host memory PSI `full avg10` was 6.45%, above
the 5% launch threshold, and a CMRU canary runner held the shared gate lock.
Therefore the P6 diagnostic R2 remains unlaunched; retry only after the
shared gate slot is available and a fresh memory-PSI preflight is at or below
5%. On launch, check once at 90 seconds, then respect the 25-minute minimum
progress-poll interval unless an earlier completion/error is expected.

### RW-466 — 2026-10-06 21:58:06 UTC — rebase P6 R2 recovery on current Assay and resolve two-gate controls

Main now includes Assay B145/B147 and source-backed Assay reports
`7.2.1.dev804+g96fbf0599`. Its `reuse.py` treats verdict schema 12 and 13 as
cold starts. On the new CIU-managed candidate worktree
`.worktrees/rg55-p6-final-r2-20261006` (based on main `e65d4e4d`, clean and
registered), the current-source plan against request base
`db29266f8a006b22a30609a74de7645d1e4c50b7` found 1,261 candidates and
classified all 1,261 `unproven-source` from the previous schema-13 verdict.
This supersedes RW-465's 1,203 witness-replay / 48 full plan, which used an
older Assay source. Its printed 105-hour estimate is the configured
600-second-per-candidate ceiling, not a measured forecast; the earlier
schema-13 campaign observed about 15 hours for 1,251 candidates. Do not claim
selective reuse on the final tree from that old artifact.

Assay's `--resume --rejudge-outcome survived` exists, but it operates on
per-tree mutation state. It can re-run the old 41 survivors only against the
exact old judged tree and matching Assay identity; that is diagnostic, not
final evidence for current main. The final current-source tree needs a
complete R2 campaign. The candidate CIU worktree carries the three-line
placement-retirement oracle for candidate 45ca, plus run-gate argv forwarding
and explicit empty pytest `addopts` needed to make targeted R2 requests
expressible; no gates were launched on it in this checkpoint.

For concurrency, the read-only `run-gate admission show` reports
`enabled=false` and no published Docker admission object in
`run-gate-project`. `tester-unified/run` itself atomically caps its own image
at two live containers, but the shared exclusive `gate.lock` used by current
controllers serializes a wider set of launchers; it is not the daemon-wide
admission mechanism. At the due 21:56Z check one CMRU coverage container had
just started, another runner was queued behind that lock, and memory `full
avg10` was 0.00%. Do not bypass the held lock. A safe estate-wide two-gate
policy needs all participating Run-Gate projects/launchers to join one
Docker-daemon ticket cap of two, plus a read of current slice capacity and
timeout/liveness behavior under that cap; no host or admission setting was
changed here.

### RW-467 — 2026-10-07 07:55:45 UTC — authorize host PID namespace for cgprofile daemon

The operator authorizes `cgprofile-host-daemon` alone to use the host PID
namespace so DAMON sysfs `pid_target` values resolve in the writer's active
PID namespace. This is a deployment change, not permission for workload,
helper, gate, or test containers to join the host PID namespace. Keep the
daemon's cgroup namespace private, network disabled, and Docker socket absent;
retain the identity-checked systemd placement/lifecycle bridge and the
invariant that no stall path signals a numeric PID. Update both RG-55 contract
mirrors and the daemon's README, design, and consumer guidance with the
security tradeoff: host PID visibility increases the daemon's process-table
and PID-operation authority, and application checks are not containment
against daemon compromise. The change is accepted only after the deployed
daemon proves live DAMON start/stop against a real host PID plus existing
socket and placement flows; do not claim those probes passed before evidence
exists.

### RW-468 — 2026-10-07 08:01:17 UTC — retain the DAMON EINVAL caveat

The existing P1 live-acceptance report records `pid: "host"` together with
`cgroup: "host"`, but DAMON `kdamond_commit` still returned `EINVAL`
(`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REPORT.md`,
§ Live acceptance and § DAMON availability). Therefore host PID visibility is
the namespace needed to make host `pid_target` IDs addressable, but is not
evidence that the kernel will accept the full DAMON context. RW-467 remains
approved; this work must test the narrower host-PID/private-cgroup deployment
and must not claim success unless a real DAMON context starts and stops. If it
still returns `EINVAL`, report the exact result as a separate kernel/config
acceptance gap and preserve profiling's verdict-neutral fallback.

### RW-469 — 2026-10-07 08:15:22 UTC — preserve profile disclosure after unavailable DAMON

The first registered cgprofile `r0-r1` run on `50372b71` ended FAIL after
2,373 passed and 8 failed. The eight failures were one stale documentation
assertion and seven direct `cgprofile serve` tests that did not set the newly
required host-PID mode. They are corrected with deployment-contract and CLI
fixture assertions. Separately, Run-Gate emitted
`profiling cleanup crashed unexpectedly: 'NoneType' object has no attribute
'get'` because a valid unavailable-DAMON summary may contain
`damon.hot_bytes: null`; the formatter discarded profile resources although
this formatter failure does not change the test verdict. Filed as RG-88 and
fixed in the same worktree with a null-safe formatter and regression test;
Run-Gate revision 56 and package gates are pending. The failed first result
is retained, not overwritten. The daemon-only host-PID change still requires
an actual deployed DAMON start/stop probe before acceptance.

### RW-470 — 2026-10-07 08:18:49 UTC — correct the placement-doc assertion

The second cgprofile `r0-r1` run on `3c77a931` passed 2,380 tests and failed
only the updated helper-read-only assertion: it expected a paraphrase rather
than the current design guide's exact contract wording. The gate completed
with Run-Gate `profile_error: null` and preserved the valid footprint record
(`damon.status=unavailable`, `hot_bytes=null`), confirming the RG-88 formatter
fix. Correct the test oracle to the exact documented wording, then rerun the
registered gate; no product-code failure was indicated by this assertion.

### RW-471 — 2026-10-07 08:37:26 UTC — CIU protects the host-singleton checkout identity

On candidate tree `b9d41a141`, cgprofile `r0-r1` passed 2,381 tests with
100% line and branch coverage, and Run-Gate's selftest passed through its
declared `cmru tester-gate` wrapper (1,700 passed, 2 skipped; 3/3 changed
executable lines covered). A direct `./run-gate.py selftest` attempt was not
a valid run: it omitted the required outer tester-unified wrapper and stopped
before pytest because `/opt/tester-venv/bin/python` is absent on the cockpit.
After building the candidate `cgprofile:local` image, `ciu up --dir .` from
this candidate worktree was refused by CIU-104 because the existing
`cgprofile-host-daemon` singleton is identity-bound to
`/workspaces/vbpub/scripts/cgroup-profiler` on main. This is intentional
cross-checkout protection, not a new CIU defect; do not rename the singleton
or bypass CIU. Verified afterward: the existing daemon remained on its old
image with private PID/cgroup namespaces and `network=none`, and
`/run/cgprofile/ctl.sock` remained present. After review and merge, rebuild
from main and deploy through `ciu up` from that checkout before live DAMON
acceptance.

### RW-472 — 2026-10-07 08:40:31 UTC — canary gate passes; old daemon DAMON remains unavailable

The registered cgroup-profiler `r3` gate passed on `ddfd693a`: all seven
canaries were rejected, zero survived. Its profile recorded DAMON as
`unavailable` with `OSError: [Errno 22] Invalid argument`. This run used the
still-active pre-change daemon, whose `HostConfig.PidMode` is private; it is
not a probe of the approved host-PID/private-cgroup candidate. Preserve the
result as baseline context and repeat a live DAMON start/stop after the
candidate is deployed from main.

### RW-473 — 2026-10-07 09:33:26 UTC — tool-version/base audit and Sol review round 7

Installed tools are Assay 8.0.0, CMRU 6.1.0, and CIU 7.16.0. The installed
Run-Gate entrypoint reports rev 55; the candidate's `run-gate.py` is rev 56
because it includes RG-88. Local `main`, local `origin/main`, and the remote
`origin/main` all remain `4670f53a67038a8a19b27ffe33e8308ec6f93fde`, the
candidate's recorded base. No rebase is needed solely because the installed
CLI versions changed. CMRU reports no cgprofile release tag and derives
`0.1.0` from the current project metadata, while the settled RG-55 release
plan says the combined first release is `1.0.0`; do not accept the implicit
`0.1.0` as a changed product decision. Use the settled explicit release
target when release gates are complete.

A fresh caller-routed `gpt-6-sol`/xhigh review returned CONDITIONAL on
candidate `2ecb3b9a`; the full artifact is
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REVIEW-round7.md`.
It found no demonstrated code blocker, but identified current docs/comments
that still described a private-PID daemon and clarified that the runtime
namespace check proves proc-view agreement, not independently that a procfs
bind is host procfs. Those corrections are now in the candidate worktree,
including the active placement-flow design and source comments. They remain
untested and unreviewed at the corrected tree; do not merge on round 7 alone.

The exact Assay 8.0.0 R2 attempt on tree `2ecb3b9a` returned
`ERROR/EXEC_FAILED` before any mutant ran: native-R2 event-counter preflight
refused because the private cgroup namespace hides the process's parent
cgroups. `.assay/verdict-r2.json` and `.assay/r2-progress.jsonl` are the
evidence; all three selected candidates remain unjudged. Assay currently
reads `/proc/thread-self/cgroup` and `/proc/self/mountinfo` directly and has
no supported environment override for an external host-ancestor view. Keep
the daemon's cgroup namespace private and do not bypass with
`--cgroupns=host`. Coordinate a supported read-only ancestor-observation
interface with the Assay workstream before restarting R2. The old daemon's
DAMON `EINVAL` and the host-PID/private-cgroup live acceptance, including
placement/socket probes, remain unresolved.

### RW-474 — 2026-10-07 09:35:40 UTC — complete round-7 documentation corrections

The round-7 findings are corrected in the current worktree: the daemon's host
PID/private-cgroup deployment is stated consistently in README, consumer and
design guidance, the current placement-flow design, the D-32 trust discussion,
Compose defaults, the image comment, and the `access.py`/`placement.py` source
comments. Historical descriptions of the earlier private-PID probes remain
historical and are not rewritten. `have_host_proc_view` is now explicitly
described as a proc-view/daemon agreement check; Compose's `pid: "host"` plus
host `/proc` bind is the managed deployment fact, not an independent runtime
host-identity proof. A structural test guards these user-facing statements.
The contract mirror comparison and `git diff --check` pass. The new review
artifact is retained as P1 round 7. No gate or test has run on this corrected
tree yet; it needs a fresh registered gate set and fix-verification review.

The outstanding R2 refusal is an Assay/runner visibility incompatibility, not
a reason to grant host cgroup namespace to the daemon or gate. Do not relaunch
until a supported read-only way to observe the candidate's real cgroup
ancestor event counters is agreed with the Assay workstream.

### RW-475 — 2026-10-07 09:45:05 UTC — file the Assay 8 / Run-Gate R2 integration gap

Assay backlog B145 explicitly requires visible ancestor event counters and
refuses native R2 when a private cgroup namespace hides them. The candidate's
Assay 8.0.0 attempt matched that contract: it passed the R0 baseline, then
returned `ERROR/EXEC_FAILED` before any candidate ran. This is not a mutant
result and not a reason to use `--cgroupns=host`. Filed Run-Gate RG-89 as an
open cross-tool integration issue: define a trustworthy exact-candidate
ancestor-event observer while preserving the gate's private cgroup namespace,
fail-closed behavior, and candidate attribution. RG-88 now has a detailed
backlog section alongside its summary row. No code or gate result is implied
by filing these entries; RG-89 and R2 remain open.

### RW-476 — 2026-10-07 09:50:21 UTC — reconcile releases against current CMRU state

CMRU 6.1.0 `status` on this candidate reports: CIU unchanged at 7.16.0,
Assay unchanged at 8.0.0, Run-Gate changed from the `run-gate-v23.10.0` tag
and due for patch release `23.10.1`, and cgroup-profiler untagged with
metadata-derived `0.1.0`. The old 23.7/23.8/23.9 targets are superseded by
the current main release baseline; do not recreate those versions. Preserve
RW-434's settled combined first cgprofile `1.0.0` release plan rather than
accepting CMRU's metadata-derived `0.1.0`; when acceptance is complete use
the supported CMRU release flow with explicit `--set-version 1.0.0`. Release
Run-Gate as 23.10.1 after its gates/review. No release was performed here.

### RW-477 — 2026-10-07 10:57:32 UTC — diagnose R1 environment failure and repair RG-85

The `gate-full` attempt on `3dad739281c4a33bbcf471093ac38f3dd5c39592`
completed its selftest, then `assay-r1` failed after 285.7 seconds: 1,585
passed, 116 failed, 2 skipped, and one error. The failures exercised real
`/proc/self/mountinfo` translation from pytest fixture repositories. The
configured `TMPDIR` and Git ceiling were the guessed `/worktree/.run-gate`,
so pytest actually fell back to private `/tmp/pytest-of-tester`; those fixture
paths had no host bind mapping. This is an environment/contract failure, not
evidence that the 116 assertions found product regressions. The exact verdict,
progress, and failed-run evidence are preserved under `.assay/` and
`.run-gate/failed/`.

The candidate now closes RG-85 at the shared Run-Gate/Assay boundary: derive
both variables from the verified state mount, pass them explicitly through
the Run-Gate assay declaration, create the default state directory for a
fresh container or bare-host Assay lane, and refuse a symlink in its place.
Regression tests cover derived paths, fresh-root creation, non-directory
refusal, and the R1/R2 passthrough contract. `assay-r1` must be rerun on the
committed candidate; RG-85 is not treated as accepted until that live lane
passes.

### RW-479 — 2026-10-07 11:07:29 UTC — cover the configured-state-root branch

The second selftest on `812a588f933e7342b7410025bb7b3e63b245089d` passed all
1,705 tests (2 skipped), but the release diff judge found 17/18 changed
executable lines and 7/8 branches: the preservation branch for an explicitly
configured host `state_root` had no oracle. Added a regression test proving
that this configured path is neither replaced with `<repo>/.run-gate` nor
created by the default-root helper. This is test-only. The selftest remains
unaccepted until its exact-tree diff judge passes; no R1/R3 run is claimed.

### RW-478 — 2026-10-07 11:03:47 UTC — correct invalid state-root preflight fixtures

The first post-RG-85 selftest on `9fa81a04c17c408a43caf20cbf826674648aa740`
finished in 160.62 seconds with 1,702 passed, 3 failed, and 2 skipped. The
three failures were fixture setup: state-preflight tests supplied a
nonexistent synthetic `repo/` while stubbing later probe results. The new
default-root creation correctly refused to invent missing parent
directories. Those tests now create the repository directory they claim to
probe; the separate missing-parent error test remains. This is a test-only
follow-up, not a change to the RG-85 behavior. Selftest and R1 are still not
accepted; rerun on the corrected committed tip.

### RW-480 — 2026-10-07 12:04:00 UTC — Sol round-8 conditional acceptance and Run-Gate repair

The caller launched a fresh `gpt-6-sol` xhigh Codex session
`01a11622-d0dc-7f83-bc0e-027acb7c5e08` against candidate
`4eb4d4c786a9514b4ae22191795fa5a6009f202e`, based on current `main` and
`origin/main` `840a9791c544326a0a63aa3ec4dcefb55e3b2aeb`. The first runner
attempt failed before repository work because its inner bwrap could not mount
`/proc`; it made no changes. The same Sol session resumed under the outer
danger-full-access runner and completed the review. Route evidence is the
caller's invocation and saved session metadata, not reviewer self-attestation.

Round 8 recorded two initial Run-Gate blockers. B1: RG-85's user-facing
CONSUMERS/DESIGN guidance still said consumers must pre-create the default
`.run-gate` root. B2: `doctor` could report a fresh default root as FAIL or a
symlink as OK, while the live lane created/rejected those paths differently;
dry-run also omitted the future state-root mount from its planned argv. The
reviewer corrected Run-Gate code, tests, README, SPEC, DESIGN-GUIDE, CONSUMERS,
and the stale CMRU comment in `10d57876f313eb26302efe990eb006b369c33f3b`.
Its targeted cockpit test selection passed 14 tests; this is not registered
gate evidence. The reviewer then committed the report as
`eb909889f31e879b14d01e6dcd2dfbd23e497983`; the final checkout was clean.
The complete record is
`scripts/cgroup-profiler/nyxloom-trove/reports/cgprofile-P1-DAEMON-REVIEW-round8.md`.

Disposition: **ACCEPT-CONDITIONAL for provisional integration after fresh
short gates on the final report-bearing tree**. The review found no remaining
code blocker in the combined P1/RG-85/RG-88 diff. It does not approve release
or shipment. The final report-bearing HEAD `eb909889` invalidates all earlier
exact-tree gates; rerun Run-Gate selftest/R1/R3 and cgprofile r0-r1/r3 and
read each result separately. Run-Gate rev 56 is the candidate; installed
Run-Gate is rev 55. Installed Assay is 8.0.0; the registered Run-Gate R1
used source-backed Assay `8.0.1.dev59+g4eb4d4c78`. CMRU 6.1.0 parses the
candidate release metadata and reports Run-Gate 23.10.1 and cgprofile's
metadata-derived 0.1.0; preserve the settled explicit first cgprofile release
1.0.0. CIU 7.16.0's config check passed but rendered zero stack configs; it
is not live deployment evidence. Main and origin/main were equal at the
candidate base, so no rebase was needed for the announced tool versions.

R2 has no exact-tree mutation result. Assay B145 refuses before candidate
execution when the test container's private cgroup namespace hides ancestor
event counters; do not use `--cgroupns=host` and do not call the refusal a
mutation result. RG-89 remains open for a supported read-only observer.
Existing `cgprofile-host-daemon` was not touched. Candidate daemon live
host-PID/private-cgroup DAMON start/stop, socket and docker-exec probes,
placement/restore acceptance, measured DAMON overhead, footprint refresh,
and both full gates remain release/closeout work. The reviewer could not
prove a loaded parent or safe isolated daemon probe from this cockpit and
launched none. These are not product-code review blockers for the authorized
provisional merge, but remain explicit release blockers.
