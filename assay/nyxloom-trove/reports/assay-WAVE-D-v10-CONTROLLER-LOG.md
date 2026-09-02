# Wave D (v10 integrity + diagnostics + M7) — controller log

Binding audit trail for the wave dispatched from
`WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md`. Entries are appended in
order; rulings are DA-Dn (in the wave prompt) and DA-Rn (review-round
rulings, here). The Wave C log (`assay-WAVE-C-go-CONTROLLER-LOG.md`) is the
precedent for shape and discipline.

- **2026-09-02 (dispatch)** — Operator instruction after Wave C shipped as
  assay-v4.1.0: *"group as much as possible into a wave and start work."*
  Controller inventory of the backlog (every `## B0nn` header read, status
  lines extracted, the uncertain ones read in full): open, unblocked and
  specifiable → B049, B053, B054, B028, B029, B060, B056, B024 (gate
  wiring), B055 (ruling), B009 (docs), B050, B051, B052, B004, B007, plus
  M7/F015. Excluded with reasons in the prompt: B020 (needs CIU v8), B023
  (no consumer, open design), B001 residual (F013 shipped), B010's
  orchestration half (run-gate's), B026 (closed by design), B048's judge
  verb. Structure: three phases on ONE branch — phase 1 schema-free fixes
  (releasable alone as 4.2.0 if phase 2 stalls), phase 2 ONE v10 cut
  carrying every wire change (B050, B053 `detail`, B004's reserved
  `PROVENANCE_UNVERIFIED`, B007, F015's claim shape), phase 3 F015. Sixteen
  rulings DA-D1..DA-D16 recorded in the prompt for the entries that asked
  for one. Two reviewers (R-1 phase 1, R-2 phases 2–3), each with its own
  3-round cap — a controller decision: one reviewer over a diff this size
  would exhaust its context before round 2.

  **B004's external blocker re-measured:** ciu 7.10.1 ships
  `[deploy.provenance] vendor_images` and `ciu provenance --json` now emits
  `schema_version: 2` with an `unlabelled` status (live run in dstdns,
  read-only), so Gate 2 of the W2 carve is passable and the W2 frozen
  assets are stale; the prompt requires re-capture before building.

  **Consumer coupling survey (fresh explorer, file:line in the prompt):** no
  consumer program parses a verdict; the lane-file schema (`schema_version
  = 2` everywhere) and run-gate's `inventory_schema == 1` check are the
  hard couplings; cmru's mutation campaign imports four assay modules by
  name from its pinned zipapp. All three are now binding constraints in the
  prompt (`LANE_SCHEMA_VERSION` stays 2; inventory additive; no renames).
  The survey also answered the operator's devcontainer question: nothing
  calls the devcontainer's installed `assay`; every gate names a pinned,
  sha256-checked in-repo zipapp with an explicit interpreter, and assay's
  own gate builds the wheel from an exact-OID clone in-container. The
  devcontainer install is an operator convenience only.

  Worktree `.worktrees/assay-wave-d-v10`, branch `feature/assay-wave-d-v10`
  from `main` at `0556d309`. Generation 1 dispatched as a fresh Opus
  implementer seeded with the wave prompt. Next free ids: **A-408**,
  **B062**.

- **2026-09-02 (generation 1 returned — B049 landed as A-408; gate PASS on
  `299d18a0`; DA-R1/DA-R2 ruled; generation 2 dispatched)** — Generation 1
  (~58 min, 92 calls) landed phase-1 item 1 only: `3b2b8e62` B049/DA-D1 as
  ONE seam, `OutputReservation._refuse_if_parent_was_replaced` inside
  `consume()` (`safeio.py:276,285`), so all five reserved-artifact reads
  inherit it (coverage, SQL R2 equivalence, ingested report, per-mutant
  equivalence, kill signal — the two `mutation.py` sites verified to
  propagate rather than fold into `crashed`); 8 red-first tests incl. two
  legitimate-state controls; README/CONSUMERS downgrade `clean: false` to
  recommended; new DESIGN-GUIDE section. Tip `36ac802c`; **gate-verified
  commit `299d18a0`** — the controller checked the LOG's own marker
  transcript (one `ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, zero
  `FAILED|DIRTY_TREE|Traceback`, `tester-unified: PASS (exit 0)`, wheel
  `assay-4.1.1.dev5+g299d18a0`, suite 3944/20); `git diff --stat
  299d18a0..36ac802c` is LOG/REPORT/BRIEF only; `git diff --name-only
  main...HEAD` touches nothing under `verdict.py`/`verify.py`/`schemas/`/
  the drift-guard. Two self-reported traps, recorded in BRIEF-1 §6 for the
  successors: a `VAR=x && setsid … &` launch backgrounds the whole list (two
  gate containers ran into one log — killed, log deleted, rerun); and
  writing the brief into the worktree while a gate ran produced
  `DIRTY_TREE` on an otherwise green run (the sharper rule: the worktree
  stays untouched for the whole run). Generation 1 red-proved with a
  path-scoped `git stash push -- src/assay/safeio.py` (not a bare stash;
  no entry left behind) — the stricter shared-stash rule is restated to
  generation 2 anyway.

  **DA-R1 (B053, accepting BRIEF-1 §4's reading):** DA-D2's "single
  handler at the run command's boundary" cannot see the 21 internal
  `except AssayError` conversions in `runner.py`; the honest form is ONE
  emitter called at every conversion site, `cli.py`'s two prints refactored
  onto it, `diagnostics=err` kept, exactly once per refusal, with a
  per-reason-code counting test enumerated from `errors.py`. Rejected: a
  CLI-boundary `try`. (a)+(b) through one seam; (c) stays phase 2.
  **DA-R2 (B054, accepting BRIEF-1 §3 option (a)):** store the offending
  arc lines on `FileCoverage` as metadata about dropped arcs (exempt from
  the arc invariants), carry them through every rebuild site, derive the
  per-file disposition; `evaluate.py` stays pure and returns skipped files
  as data for `runner` to print on the diagnostics stream. Rejected: the
  `(profile, defects)` parser-signature change. Doing B053 with B054 so
  the stream plumbing lands once is allowed.

  **Generation 2 dispatched** — fresh Opus, seeded with BRIEF-1 + the two
  rulings, phase-1 items 2–10 in order, BRIEF-2 when phase 1 is
  gate-green (then R-1 + generation 3). Next free ids **A-409** / **B062**
  (main unchanged at `a4a865da`).

- **2026-09-02 (generation 2 returned — 7 of 10 phase-1 items; gate PASS on
  `c80b3452`; DA-R3..DA-R6 ruled; generation 3 dispatched)** — Generation
  2 (~74 min, 263 calls) landed `440d5da9` B053 (a)+(b)/A-409 (one emitter
  `runner.announce_refusal` at 13 conversion sites + `cli.py`'s three
  prints; `evaluate_r1` gained `diagnostics`; 9 tests red-first),
  `c37ca3fb` B054/A-410 (`FileCoverage.contradictory_branch_lines`,
  parser drops the arcs and records the lines, `evaluate` refuses only
  for a judged file, `runner` names every defective record on diagnostics;
  7 tests red-first; two old verdict-wide-refusal tests rewritten),
  `c80b3452` B060/A-411 (staging under a `TemporaryDirectory`, outcome
  test), B056/A-412 (option 1), B055/A-413 (ruling + docs), B009 (docs —
  and the entry's premise measured FALSE: all four consumers still vendor
  a pinned `.pyz`; the docs describe that). Tip `10d9390d`;
  **gate-verified commit `c80b3452`** — the controller checked the LOG's
  marker transcript (one `ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`,
  zero red flags, wheel `assay-4.1.1.dev9+gc80b3452`, `PASS (exit 0)`,
  suite 3968/20); `c80b3452..10d9390d` is BRIEF/LOG/REPORT only; nothing
  under `verdict.py`/`verify.py`/`schemas/`/the drift-guard; no `!`. Main
  moved to `9b0bca62` (a ciu backlog filing; assay untouched). BRIEF-2
  corrects BRIEF-1 twice (B029: ONE `execute_command` caller; B028's
  likely catch is the existing handler at `runner.py:3776`) and adds a
  third trap (never run git after `cd /workspaces/vbpub`).

  **DA-R3 (B053, ask 1): yes, and in phase 1, not phase 2.** The emitter's
  contract is a message, not an exception: the five refusal sites that
  call `refuse_lane`/`refuse_all` with a bare `(status, reason_code)` —
  `DIRTY_TREE` (both), `HEAD_CHANGED`, `MISSING_EXTERNAL_TOOL`,
  `env_required`, the bad `--shard` — compose their message where the
  fact is known (the offending paths, the old and new HEAD, the tool, the
  variable, the shard spec) and go through the same emitter; a typed
  `AssayError` only if it simplifies the site. R-1's stated push ("every
  refusal reachable through `assay run` prints exactly one line") must
  hold without qualification before R-1 is dispatched.
  **DA-R4 (B053, ask 2): not correct as landed.** A line about a refusal
  that never reaches the verdict is a sentence the consumer cannot
  reconcile with the document; defer that one site's announcement to
  where the final claim is chosen (`runner.py:~3134`), no general buffer.
  **DA-R5 (B056, ask 3): option 1 stands (DA-D13).** No second wheel build
  for a docstring's negative; R-1 may argue, the controller will not
  reverse on cost grounds alone.
  **DA-R6 (B029, ask 4): measure first, as proposed.** Drive a real R3
  lane with a resolvable `derived:` fact through the installed CLI at the
  tip. If the misattributed `ERROR`/`BAD_LANE_CONFIG` reproduces on the
  shipped isolated path, fix where that path builds the side-run's plan
  (wherever it is) and land the CLI test. If it does NOT reproduce, thread
  the parameters through `execute_command` anyway (the docstring names
  the defect and the legacy path is public), correct the docstring, land
  the CLI-driven test as a regression guard (R3 claim PASS/FAIL with a
  derived fact), and mark B029 RESOLVED-by-measurement with the
  transcript, noting the entry's premise was confined to the legacy path.

  **Generation 3 dispatched** — fresh Opus, seeded with BRIEF-1+2 and
  DA-R3..R6: B028, DA-R3/DA-R4 follow-ups, B029, B024, then gate, BRIEF-3,
  return (phase 1 complete → R-1 + generation 4 for phase 2). Next free
  ids **A-414** / **B062**.

- **2026-09-02 (generation 3 returned — PHASE 1 COMPLETE 10/10; gate PASS
  on `93188912`; DA-R7 ruled; R-1 and generation 4 dispatched in
  parallel)** — Generation 3 (~65 min, 230 calls) landed `21bdf19d` B053
  follow-ups/A-414 (six formerly silent refusal sites compose their
  message where the fact is known and go through `announce_refusal`; the
  discardable early-R2 refusal is announced only where the surviving claim
  is chosen; red-first 7F/10P), `dd8f4d2c` B028/A-415 (measured first: the
  higher-rigor entry point already wrote its verdict; only direct R0 exited
  without the reserved `--verdict-json`, so ONE boundary was added, with
  the post-command guard moved verbatim into `_finish_direct_r0_lane`;
  BRIEF-2's guess about `runner.py:3776` was wrong the other way — that
  handler laundered a `LANE_TIMEOUT` cleanup failure into `GIT_FAILED`,
  now fixed; real `budget = "1s"` + `sleep 30`; red-first 3F/4P),
  `81228b25` B029/A-416 (RESOLVED BY MEASUREMENT per DA-R6: the predicted
  misattributed R3 claim does not reproduce on the shipped isolated-canary
  path, which never reaches `execute_command`; the threading landed on the
  legacy path anyway, docstring corrected, the CLI test labelled a
  regression guard), `93188912` B024 (DA-D15's escape hatch: the image
  carries neither `pyflakes` nor `ruff`, the wheelhouse holds exactly the
  five build wheels, the gate has no other ingress; NOTHING landed;
  decision ask with three options). Tip `b90ca598`; **gate-verified
  commit `93188912`** — controller checked the LOG's marker transcript
  (one `ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, zero red flags,
  wheel `assay-4.1.1.dev14+g93188912`, `PASS (exit 0)`, both v9 schema
  phases, suite 3985/20); `93188912..b90ca598` is BRIEF/LOG only; nothing
  under `verdict.py`/`verify.py`/`schemas/`/the drift-guard; zero `!`
  markers. Main moved to `f43da249` (run-gate RG-32 and ciu CIU-90 docs;
  assay untouched).

  **DA-R7 (B024): option (b), pyflakes only, in its OWN closure.** Add
  `gate/distribution/lint-requirements.txt` (`pyflakes==<pin> --hash=…`)
  and `gate/distribution/lint-wheelhouse/` holding that one pure-Python
  wheel (~100 KB), fetched ONCE on the devcontainer with `pip download
  --no-deps`, sha256 recorded in the requirements and a manifest beside
  the existing `build-wheelhouse-manifest.json`, committed. The gate
  installs it `--no-index --require-hashes` into a THIRD venv, `lint-venv`
  — never `build-venv` or `run-venv`, so A-198's five-wheel closure
  assertion is byte-for-byte untouched — and runs `python -m pyflakes
  src/assay` as a phase AFTER the suite; pyflakes' whole rule set is the
  F-rule set DA-D15 asked for. `ruff` is dropped: its platform binary
  wheel is ~10 MB and adds nothing over pyflakes for F-rules. Rejected:
  (a) the shared image (outside `assay/**`, re-risks four other products'
  gates for a linter), (c) outside the gate (B024's own rejection —
  nothing that is not part of the verdict counts). Generation 4 lands it
  FIRST, before the cut, so the phase-1 fallback release carries it; R-1
  verifies it in its fix-verification round (its round-1 tip predates it).

  **R-1 dispatched** — fresh Opus, blind pass on `a4a865da...93188912`
  first, the wave prompt's R-1 push list plus the DA-R rulings, own
  detached worktree for probes, registered gate on the tip itself, report
  to the scratchpad as `assay-WAVE-D-v10-REVIEW-R1-round1.md`, 3-round
  cap. **Generation 4 dispatched** — fresh Opus, seeded with BRIEF-1+2+3
  and DA-R7: B024 first, then phase 2's design step (A-rows for B050,
  B053 `detail`, B004 with re-captured ciu 7.10.1 assets, B007 with a
  measured materialisation cost, F015), then the single `feat(assay)!:`
  cut, then B050 → B051 → B052 → B053 `detail` → B004 → B007 → migration
  notes. Next free ids **A-417** / **B062**.

- **2026-09-02 (HOST LOAD INCIDENT — throttled; standing rule added to the
  wave prompt)** — A dstdns peer session reported the host's 1-minute
  load spiking from ~13 to ~80 within two minutes, CPU PSI avg10 47.65%,
  every top consumer under this session's scratchpad, and reminded that
  the host runs a production game server the operator had flagged as
  degraded by exactly this pattern earlier the same day. Measured by the
  controller: load 85.28 on 8 cores; R-1's pytest-xdist workers
  (`popen-gw0..gw3`) running the distribution-build tests (each builds a
  venv and pip-installs) on the host, concurrently with generation 4's
  registered-gate container (`dev-background.slice`, no `--cpus` cap) and
  the dstdns side's own schema gate. Actions, all reversible, none killing
  an in-flight run: reniced (19) + ionice'd (idle) 50 host-side processes
  of R-1's run; `docker update --cpus=3` on the running gate container;
  messaged R-1 and generation 4 with the standing rule (serial pytest under
  nice/ionice, whole suite at most once per checkpoint, ONE gate container
  at a time across all agents, cap every new gate container right after
  launch, no build step concurrent with a suite run); answered the peer
  with what was done and the expected durations; a Monitor watches load
  for 12 minutes with SIGSTOP/SIGCONT of R-1's test tree as the next
  escalation if it stays above 20. The rule is now in the wave prompt's
  RULES block (commit on main) and in memory
  (`host-shared-with-production-load-rule`). Backlog candidate for a later
  wave: a `--cpus` cap in `tester-unified-gate.sh`'s own `docker run`.
  **Escalation 16:51** — renice alone left load at 87 (CPU PSI avg10 88 %,
  292 MB free): the reviewer had launched SEVEN concurrent full-suite
  mutation probes (`mut1..mut7`, each `-n 4`, each distribution test
  building a venv and pip-installing) on top of the gate. Controller
  SIGSTOPped six of the seven and a detached serializer
  (`scratchpad/serialize-r1-muts.sh`) now CONTs the next only after the
  previous log carries its `MUTEXIT=` line; load 87 → 54 within a minute,
  PSI 88 → 40 %. No run was killed, so the mut logs stay valid evidence.
  Rule tightened for reviewers: one pytest invocation alive at a time,
  targeted test files per mutant rather than the whole suite. R-1 and the
  peer were told; the peer resumed its own queue at its discretion.
