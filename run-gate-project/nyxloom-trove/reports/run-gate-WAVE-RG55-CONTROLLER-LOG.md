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

## Dispatch

| package | worktree | branch | implementer | reviewer | status |
|---|---|---|---|---|---|
| P1 — cgroup-profiler daemon | `.worktrees/rg55-profiler-daemon` | `rg55-profiler-daemon` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | sessions 1–5 → C0–C9 + live acceptance (`8cdd09e6`, RW-19 `71c6f607`); orphaned r2 container exited 15:49Z (208 candidates, 195 killed, 13 survived on the OLD tree `7ec4f9e8`); `budget_per_candidate` key misplaced in `16f3a29f` → fixed `5ce232d1`; r2 RESUME under run-gate ownership running since ~15:58Z (container `run-gate-vbpub-r2-680904-…`, tip `2c62f4db`); survivor triage + final gates + reviewer pending the verdict |
| P6 — cgroup-profiler follow-ups (CP-2 socket, CP-4..CP-7, cgprofile.slice, CP-8 watch, CP-9 placement) | `.worktrees/rg55-followups-cgprofile` | `rg55-followups-cgprofile` | fresh Sonnet (checkpoint clause on, HARD) | fresh Opus xhigh (never a fork) | dispatched 2026-09-12 ~15:25Z from the P1 tip (RW-35); session 1 → C1 CP-4 `376bb9cb`, C2 CP-5 `16b01c1c`, BRIEF-2 `614dcd9f`; session 2 → C3 CP-7 `907ddd50`, C4 CP-6 `e053276b`, BRIEF-3 `36859c77`; session 3 → C5 `39d43934`, BRIEF-4 `c60644ac`; session 4 (Opus) → C6 socket carrier `bb575fd4` (35 tests, parity harness, PROTOCOL.md), BRIEF-5 `b865556b`; session 5 (Opus) dispatched ~16:30Z for C7 (+C8), r0/r1 lane per RW-39; release cgprofile 1.1.0 after review |
| P7 — assay B091 (progress-judged candidates) | `.worktrees/assay-liveness` | `assay-liveness` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | dispatched 2026-09-12 ~13:55Z from `main` (RW-29); session 1 → A1 (`de32bb91`), BRIEF-1 `723c431d`; session 2 → spike + plugin + v1 runner (`f4fa1788`), BRIEF-2 `ef5088f6`; session 3 → RW-36 gating `e27b107b` + verify.py fix, BRIEF-3 `d2b7c76d` (A3 mechanism decided: `LivenessHungExpired` + `ReasonCode.CANDIDATE_HUNG`); session 4 → A3 active runner + `hung` bucket `44dd12ca` (a real `judge_mutation` precedence bug fixed; 252 calls — clause violated, flagged), BRIEF-4 `4ace234f`; session 5 dispatched ~16:35Z for A3 e2e/mutants, A4, A5, A6 |
| P8 — mdt host-setup `dev-gates.slice` (dev-infra withdrawn, RW-30) | `.worktrees/mdt-dev-slices` | `mdt-dev-slices` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | dispatched 2026-09-12 ~13:55Z from `main` (RW-29); tip `7bd2f03c` review round 1 ACCEPT-conditional B1–B6 (~14:50Z) → repair set + M5 landed `ae38d55a` (~15:30Z, gate green); round 2 ACCEPT-conditional (B7/B8 new, B1–B6 + M5 PASS, RW-37) → repairs `e326cc9b` (gate green, ~16:00Z); round 3 ACCEPT (~16:10Z) → MERGED `a71c46b0` (RW-38); operator installs on the host BEFORE any devcontainer rebuild |
| P4 — run-gate follow-ups (RG-57..61) | `.worktrees/rg55-followups-run-gate` | `rg55-followups-run-gate` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | dispatched 2026-09-12 ~13:00Z from `186461de` (RW-27); C1–C4 committed (`a6716422`, `b5e4a9c6`, `e698835f`, `c37b6e94`); session 2 → merged `rg55-run-gate-client`@`647a2cc6` (`0c782601`), C5 `5b80c024` (RG-61 sweep except item 5, budget 900s, rev 42), BRIEF-2 `b695db00` (304 calls — clause violated); session 3 dispatched ~16:45Z for selftest/footprint transcript/r1/r3, r2 after P2's resume |
| P2 — run-gate client | `.worktrees/rg55-run-gate-client` | `rg55-run-gate-client` | fresh Sonnet (checkpoint clause on) | fresh Opus xhigh (never a fork) | sessions 1–7 → C1–C8 complete (`62d9a66a`, rev 41, footprint manifest from a live probe); review round 2 ACCEPT on `186461de` (close-out commits); assay-r2 hit the 4 h lane budget at ~165/283 (16:28Z) → RESUMED untracked (RW-40); survivor triage + final gates + merge/release 23.7.0 pending the verdict |
