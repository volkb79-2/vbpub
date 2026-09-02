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
