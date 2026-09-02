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
