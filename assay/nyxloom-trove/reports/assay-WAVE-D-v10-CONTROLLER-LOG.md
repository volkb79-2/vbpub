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
