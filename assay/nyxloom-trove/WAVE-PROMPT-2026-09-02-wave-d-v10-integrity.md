# Assay Wave D — everything open, one branch, one cut (target assay-5.0.0, verdict schema v10)

**Written 2026-09-02 by the controller, after Wave C shipped as assay-v4.1.0.**
Operator instruction, verbatim: *"group as much as possible into a wave and
start work."* This wave therefore carries every assay-side backlog item that
is open, unblocked and has a specifiable contract, in three phases on ONE
branch, so the estate pays ONE schema migration (the argument B007's own
sequencing note makes, twice) and one review cycle per phase.

Controller log: `reports/assay-WAVE-D-v10-CONTROLLER-LOG.md` (binding audit
trail; every ruling below is restated there when applied).

## Status

- 2026-09-02: generation 1 dispatched on `feature/assay-wave-d-v10`
  (`.worktrees/assay-wave-d-v10`) from `main` at `0556d309` (post-4.1.0).

## Sequencing, and why

| phase | schema | contents | releasable alone? |
|---|---|---|---|
| **1 — fixes with no wire change** | v9 unchanged | B049 · B054 · B053 (stderr + diagnostics halves) · B028 · B029 · B060 · B056 · B024 (gate wiring) · B055 (ruling + docs) · B009 (docs) | **YES** — the controller may cut it as 4.2.0 if phase 2 stalls; keep it that way (no half-landed schema work before the cut commit) |
| **2 — the v10 cut** | **v10**, one `feat(assay)!:` commit, then the items that ride it | B050 · B051 · B052 · B053 (the `detail` field) · B004 (`PROVENANCE_UNVERIFIED` + the adjudicated provenance integration) · B007 (ordered multi-target R3 canary) · migration notes | only as a whole |
| **3 — M7 / F015** | on v10 (its claim shape is DESIGNED in phase 2, before the cut, and lands here) | `fail-before/pass-after` as a Tier 1 computed method | only after phase 2 |

Why one cut: B050, B053's `detail`, B004's reason code, B007's payload and
F015's claim are each "a wire field at the next schema cut" in their own
entries. Cutting v10 for one and v11 for the next repeats the mistake B007's
sequencing note was written to prevent. **Every wire change is designed
before the cut commit and lands in it or immediately after it on v10; no
second bump in this wave.**

Deliberately NOT in this wave (do not build, do not design): **B020** (SQL
template/reset hooks — needs CIU v8, which is not built); **B023** (shard
merge producer/consumer — no consumer is asking and its design question is
open; a decision ask is welcome, code is not); **B001**'s residual
qualification (F013 is shipped); **B010**'s orchestration half (run-gate's,
not assay's); **B026** (closed by design, A-309/A-310); **B048**'s detached
`assay judge` verb; Go R2/R3; any `assay canary qualify` document kind (B007
entry's last bullet — write a decision ask if you think it is needed).

## Roles

- **Implementer:** fresh sessions, Opus xhigh, one generation at a time,
  E-008 checkpoint clause (below); each generation writes the next BRIEF.
  Never a fork.
- **Reviewers:** TWO fresh Opus xhigh sessions, spawned when the phase they
  own is gate-green, each resumed via SendMessage for its own later rounds,
  3-round cap each: **R-1** owns phase 1 (runtime/isolation fixes, the gate
  wiring, the docs), **R-2** owns phases 2–3 (the cut, `verify.py`
  independence, Tier 2, B007, F015). This is a controller decision recorded in
  the controller log: one reviewer over a diff this size would exhaust its
  context before round 2.
- **Controller:** merges `--no-ff`, runs `cmru release --project assay`
  detached from `/workspaces/vbpub`, deploys the wheel into
  `/home/vscode/.venv` (sha256 against the sidecar), clears `[Unreleased]`,
  drops the dstdns release-notify, updates memory. Decision asks come to the
  controller; the controller rules them in the log as DA-Dn rows.

## Rulings (controller, 2026-09-02) — record each as an A-row when applied

Every backlog entry below asked for "a ruling recorded as an A-row". These
are the rulings. Cite this file and the DA-D id in the row; name the rejected
alternatives the entry lists.

- **DA-D1 (B049):** option (4). At `consume()` time, `os.fstat(parent_fd)`'s
  `st_nlink == 0` on the ALREADY-HELD descriptor means the tool replaced its
  output directory → `ERROR`/`UNREADABLE_ARTIFACT` with a message naming the
  cause ("your tool deleted and recreated `<dir>`; assay's reservation was
  orphaned; set the tool's clean/rm-first option off or write into the
  existing directory"). Same rule at ALL THREE sites the entry names
  (coverage read, SQL R2 `equivalence_artifact`, `mutation.py`'s absent-read
  → `crashed` fold). Regression test: a 15-line directory-recreating fake
  tool, no Vitest. CONSUMERS' `clean: false` note becomes "recommended; if
  you forget, assay names the cause instead of reporting no coverage".
  Rejected: (1) re-open by name (loses TOCTOU cover), (3) document only.
- **DA-D2 (B053):** three halves. (a) **CLI, phase 1:** every `AssayError`
  that becomes a verdict prints ONE stderr line `assay: {outcome}/{reason_code}: {message}`
  through a single handler at the `run` command's boundary — the "narrower
  fix" in the entry — with the existing three-line summary unchanged.
  (b) **Library, phase 1:** the same text goes to the existing `diagnostics`
  stream `environment_command` already uses (Wave C DA-R3's candidate
  mechanism), so a library caller gets it without stderr. (c) **Wire, phase
  2 (v10):** a per-claim optional `detail` string on NON-PASS claims only,
  byte-copied from the refusing `AssayError`'s message, bounded at 2048
  bytes with B014's truncation convention (`dropped_bytes`), absent on PASS
  and absent when no refusal produced text; `verify.py` checks the presence
  rule and the bound only — free text is diagnostic, declared-not-verified,
  and the schema description says so in `producer_tool`'s words (A-230a).
  Rejected: stderr-only (ciu's `LaneResult` reads the document, not stderr).
- **DA-D3 (B054):** per file, on DA-R2's principle (A-405 for Go): an
  istanbul record whose `branchMap` contradicts its own `statementMap`/`s`
  classification is a defect of THAT FILE. A file with no line in the judged
  set is skipped and NAMED on the diagnostics stream (never silently); a
  file inside the judged set refuses `ERROR`/`UNREADABLE_ARTIFACT` naming the
  file and the arc line (today's message, per file). A-357's refusal on an
  unrecognised arc TYPE is untouched. Rejected: a v10 `excluded_files` wire
  list — DA-R2 ruled the not-judged set invisible by construction and no
  consumer asks for the list; reconsider only if R-1 shows a consumer needs
  it.
- **DA-D4 (B051):** `discarded` means **listed** — the count of mutants the
  ingested report itself marks `CompileError`/`RuntimeError`. Derive it in
  `ingest_mutation_report`, re-derive it in
  `verify._check_ingested_r2_agrees_with_its_payload` beside the other three
  facts, refuse a document whose `discarded` disagrees (the `9999` test).
  The schema description is corrected to say exactly that and that mutants
  the tool dropped before reporting are NOT counted anywhere. Witness: a
  REAL Stryker report with a non-zero `discarded`, produced in-image from the
  Wave B probe-js harness (a deliberately uncompilable mutant); if Stryker
  cannot be driven offline in `tester-unified:local`, that is a decision ask,
  not a hand-edited fixture (A-334). Rejected: "encountered" — not derivable
  from any artifact assay receives, so a declared-not-verified count that
  says nothing checkable.
- **DA-D5 (B052):** a third non-repudiation tier, **content**: at ingest,
  read each measured path's committed bytes through
  `SnapshotRepository.read_regular_file` inside the baseline snapshot block
  `_ingest_r2_report` already runs in, and compare with the report's
  `source` under a STATED normalisation — line endings folded to `\n`, one
  trailing newline ignored, everything else byte-exact. Mismatch →
  `ERROR`/`UNREADABLE_ARTIFACT` naming the file and the three causes the
  entry lists (stale report, foreign report, a tool that rewrote the source
  before mutating) plus the remedy (run the tool inside the lane against the
  committed tree). No wire field; no warning mode; no opt-out key. Case (2)
  (rewritten-in-flight source) is REFUSED by design and the test asserts it:
  evidence whose text is not the commit's is not evidence about the commit,
  and a formatter writing back would trip `DIRTY_TREE` anyway. Rejected:
  warn (nobody reads it), record-on-the-wire (records a document assay has
  already decided not to trust).
- **DA-D6 (B050):** exactly as the entry's "The fix" section: `judgment.r2.fail_under`
  required under `producer = "ingested"`, forbidden under `"native"`;
  `judge_mutation` takes the floor; `verify.py` reads it FROM the document;
  the load-time refusal deleted; CONSUMERS' "must be 100.0" paragraph dropped.
- **DA-D7 (B004):** Gate 1 passes with this cut — `PROVENANCE_UNVERIFIED`
  (A-276, reserved by name) lands in v10 in the `NO_MEASUREMENT` set, plus
  the §5.4 narrowing (`adjudicated` ⇒ `verified_by_assay: false` in
  `__post_init__` AND the JSON schema `else` branch, since the bump is being
  paid). Gate 2: ciu 7.10.1 ships `[deploy.provenance] vendor_images`
  (ciu CHANGES.md ~line 1869) and `ciu provenance --json` now emits
  `schema_version: 2` with an `unlabelled` container status — the W2 frozen
  assets (ciu 6.0.3, schema 1) are STALE and must be re-captured from the
  real verb before anything is built against them (`cd /workspaces/dstdns &&
  ciu provenance --json` is read-only and works here). Build W2–W7 of
  `W2-CARVE-B004-provenance-verified.md` as written, with `mismatch →
  NO_MEASUREMENT` (carve §4.1), `judge.adjudication_dir` + `judge.evidence
  = [{source = "adjudicated", name = "image-provenance"}]`, `LANE_SCHEMA_VERSION`
  stays 2 (additive optional key — ruled, not open). The PASS oracle
  (`verified-match`) must be proven against a document ciu's REAL code
  produced (its own test harness's real output, or a live correctly-deployed
  instance), never a hand-written one; if neither is reachable, that
  criterion stays `absent` with the reason and the item still ships (every
  non-green path is live-provable today). Also correct A-O12's false
  `declared_unverified` claim (carve W0).
- **DA-D8 (B007):** build it, per the entry's "Design findings" as
  constraints: `judge.canary.targets` (ordered, bounded — MEASURE one
  materialisation before choosing the bound, record the number),
  `aggregation = "any" | "all"` (closed), a per-attempt payload array with a
  closed "why not attempted" vocabulary, `judgment.r3` gaining
  `targets`/`aggregation`, short-circuit bookkeeping under `any`,
  `verify.py` recomputing the aggregation independently (hand-transcribed,
  the project's discipline), budget exhaustion stays its own terminal, the
  B005 interaction rule ("every canary target ∈ `judge.targets`" on a
  `whole_target` lane) specified and tested, and CONSUMERS' vacuity paragraph
  for `any`. The single-target form stays valid (a one-element list is the
  migration for every existing R3 lane; say so in the migration notes). No
  `assay canary qualify` document kind this wave.
- **DA-D9 (F015 / M7):** design first, in phase 2, as A-rows: the lane
  declaration (which test(s), which "broken" commit — default the lane's
  resolved base), the claim's wire shape (a new `judgment.*` block and claim
  kind on v10, designed so `verify.py` re-derives the status from the two
  recorded outcomes), and the mechanism: materialise the pre-fix commit WITH
  the declared test files overlaid from HEAD (the canary's variant-commit
  path inverted), run the declared test there (must FAIL) and at HEAD (must
  PASS); anything else is `NO_MEASUREMENT`-class with an existing or
  reserved code. Tier 1, deterministic, no new substrate. Implement in
  phase 3; mark F015's acceptance `proven` with evidence and M7 `done` in
  the roadmap only when both are real.
- **DA-D10 (B028):** one outer catch per higher-rigor entry point
  (`_run_higher_rigor_lane`) and one for direct R0's own loop: a lane-wide
  `LANE_TIMEOUT` becomes the refusal claim the existing refuse path already
  builds, the reserved `--verdict-json` is WRITTEN, cleanup of a half-built
  snapshot is attempted and its failure is recorded via the existing
  cleanup-failure path (never masks the timeout). Test red-first through the
  installed CLI with `budget_seconds = 1` and a real slow command.
- **DA-D11 (B029):** yes — the R3 canary side-run resolves infrastructure
  facts exactly as the main command does; thread
  `infrastructure_source`/`infrastructure_environment` through
  `execute_command` into `canary.py`'s two call sites; CLI-driven test with a
  resolvable `derived:` fact asserting the R3 claim is PASS/FAIL on the
  canary, not `ERROR`/`BAD_LANE_CONFIG`.
- **DA-D12 (B055):** leave as the documented limit (alternative 1 of three);
  no wire field. CONSUMERS' Go paragraph states "statement-granular to the
  line, not to the statement" beside what a Go R1 claim means; the test that
  asserts today's behaviour stays.
- **DA-D13 (B056):** option 1 — correct the docstring to the measured truth
  and assert the OUTCOME; apply the same to the Go helper's packaging test
  (already in that shape — verify, do not re-write).
- **DA-D14 (B060):** remove the staging tree (build under a
  `TemporaryDirectory`); an outcome test: a build with `--outdir
  <repo>/assay/dist` leaves no untracked path.
- **DA-D15 (B024):** wire `pyflakes` and `ruff` (pyflakes-equivalent rule
  set only: F-rules, no style rules — say which in the A-row) into the
  registered gate as a phase AFTER the suite, inside the image if the tools
  are there, else inside `run-venv` from the offline wheelhouse if the
  closure already carries them; if neither is possible without a network
  fetch, write the decision ask and land nothing (A-198's hash-bound closure
  is not to be loosened for a linter).
- **DA-D16 (B009):** docs only. Item 1 verbatim (assay.toml's role).
  Item 2: describe the distribution model AS MEASURED today — which
  consumer vendors a pinned `.pyz`, which bakes, which builds in-repo (grep
  `dstdns/tools/assay`, `cmru/cmru.toml`'s S15 pin, `ciu`, `nyxloom`,
  `run-gate`); do NOT prescribe the "vendoring retired" future the entry
  proposes unless you find it is already true. Item 3: a one-line forward
  pointer, no more.

## Consumer coupling facts (measured 2026-09-02, controller's survey) — binding

No consumer program parses a verdict document: run-gate takes the lane's
outcome from the exit status and treats `--verdict-json` as an opaque path
(`run-gate-project/run-gate.py:1207-1211,2330`). The couplings that DO exist:

1. **The LANE-file schema is the hard coupling.** Every consumer declares
   `schema_version = 2` (`ciu/assay.toml:18`, `cmru/assay.toml:13`,
   `nyxloom/assay.toml:27`, `assay/assay.toml:21`, `dstdns/assay.toml:26`).
   **`LANE_SCHEMA_VERSION` stays 2 for the whole wave**: every lane-file change
   (B004's `adjudication_dir`/`adjudicated`, B007's `targets`/`aggregation`,
   F015's declaration) is additive and optional, and the single-target R3
   form keeps loading unchanged.
2. **run-gate refuses `inventory_schema != 1`** from `assay lanes --json`
   (`run-gate.py:1755-1760`) and derives `--request-base` delegation from
   that inventory. `inventory_schema` stays 1; new inventory keys are
   additive only.
3. **cmru's mutation campaign imports assay as a library** from its pinned
   zipapp: `assay.diff.parse_added_lines`, `assay.git.resolve_base/head_rev/run`,
   `assay.mutation.resolve_mutation_targets`, `assay.adapters.python.PythonAdapter`
   (`cmru/tools/mutation_campaign.py:141-150`). Do not rename or re-sign those.
4. **dstdns's handoff contract reads verdict fields by NAME**: `outcome`,
   `status`, `coverage.pct`, `coverage.missing_lines`,
   `coverage.missing_branch_lines`. v10 may add fields; it does not rename
   these.
5. Every consumer pins its own pyz (ciu 3.2.0, cmru 2.3.0, nyxloom 4.0.0,
   dstdns 4.0.0), so 5.0.0 reaches nobody until they re-pin; the migration
   notes are what they re-pin against.

## Implementer prompt — generation 1

```text
You are the implementer for assay WAVE D (target release assay-5.0.0, verdict
schema v10) in /workspaces/vbpub. Fresh session: you inherit nothing, you
verify everything you claim.

READ FIRST, IN FULL, IN THIS ORDER
1. /workspaces/vbpub/AGENTS.md and /workspaces/vbpub/CLAUDE.md.
2. assay/README.md; assay/docs/DESIGN-GUIDE.md (whole); assay/docs/CONSUMERS.md
   (whole — you will edit it, including a "Migration notes (v9 → v10)" section).
3. assay/nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md (this
   file) — the Rulings section is binding.
4. assay/nyxloom-trove/4-backlog.md: B049, B053, B054, B028, B029, B060, B056,
   B024, B055, B009 (phase 1); B050, B051, B052, B004, B007 (phase 2); the
   product definition's F015 (phase 3). Read B023/B020 only to NOT build them.
5. assay/nyxloom-trove/W2-CARVE-B004-provenance-verified.md (whole) and
   reports/assay-B004-carve-review-fable.md.
6. assay/nyxloom-trove/decisions.md: the Open table (A-O05..A-O12), A-078,
   A-138/A-170 (hard cut), A-182, A-204, A-230a, A-244, A-254..A-256,
   A-270, A-275/A-276, A-334, A-335, A-357, A-360, A-379/A-380, A-405..A-407.
7. assay/nyxloom-trove/WAVE-PROMPT-2026-08-30-js-consumer-producer.md §"Wave B
   — what changes in the prompt" (the v8→v9 cut mechanics you repeat for v10)
   and reports/assay-WAVE-C-go-CONTROLLER-LOG.md (how the last wave ran:
   generations, briefs, gate discipline, what went wrong).
8. The Wave B and Wave C REPORTs' "what a reviewer should push on" sections.

WORKTREE (already created by the controller)
  /workspaces/vbpub/.worktrees/assay-wave-d-v10, branch feature/assay-wave-d-v10,
  from main at 0556d309. Work ONLY inside it. Touch ONLY assay/** (the srdm
  tree, ciu, cmru, dstdns are read-only for you).

SCOPE — PHASE 1 (no wire change; keep it releasable on v9 at every commit)
  1. B049  DA-D1 — three sites, one rule, one fake-tool regression test.
  2. B054  DA-D3 — per-file istanbul disposition.
  3. B053  DA-D2 (a)+(b) — the stderr line and the diagnostics stream.
  4. B028  DA-D10 — LANE_TIMEOUT writes a verdict.
  5. B029  DA-D11 — canary side-run sees infrastructure.
  6. B060  DA-D14; B056 DA-D13; B024 DA-D15; B055 DA-D12; B009 DA-D16.
  Gate green on the phase-1 tip; LOG/REPORT; a BRIEF; return (the controller
  dispatches R-1 on that tip while generation 2 continues into phase 2).

SCOPE — PHASE 2 (design every wire change FIRST, then ONE cut commit)
  7. Design, as A-rows BEFORE any schema edit: B050's field (DA-D6), B053's
     `detail` (DA-D2 c), B004's reason code + §5.4 narrowing (DA-D7), B007's
     `targets`/`aggregation`/per-attempt payload (DA-D8), F015's claim shape
     (DA-D9). Then the cut: VERDICT_SCHEMA_VERSION = 10 (hard cut, A-138/
     A-170; `assay verify` refuses v9 exactly as v9 refused v8), every field
     registered in the schema, the dataclass AND verify.py (the third place —
     the 2.4.0 lesson), new frozen drift-guard carve-assets/W6/
     verdict.schema.v10.json + expected/ + test_acceptance_v10.py with W5
     kept as history, `feat(assay)!:` on exactly ONE commit.
  8. B050 (DA-D6) → B051 (DA-D4) → B052 (DA-D5) → B053 detail (DA-D2 c) →
     B004 (DA-D7; W2–W7 of the carve, re-captured ciu 7.10.1 assets first) →
     B007 (DA-D8).
  9. CONSUMERS "Migration notes (v9 → v10)": the one-element `targets` list
     for R3 lanes, `judgment.r2.fail_under` for ingested lanes, v9 verdicts
     refused by `assay verify`, the new refusals (B052 content check, B054
     per-file), `detail` present on refusals, the adjudicated provenance lane
     shape with the `|| true` example. Python and Go lanes without R3:
     unchanged.

SCOPE — PHASE 3
  10. F015 per DA-D9's design; 2-product-definition.md F015 `proven` with
      evidence, 3-roadmap.md M7 `done`; README/DESIGN-GUIDE/CONSUMERS.

NOT IN SCOPE — STOP AND WRITE A DECISION ASK IN THE REPORT INSTEAD
  - a second schema bump; any wire change not designed under step 7.
  - B020, B023, B001 residual, B010's orchestration half, B048's judge verb,
    Go R2/R3, `assay canary qualify`, anything in ciu/cmru/dstdns/srdm.
  - loosening the gate's hash-bound build closure for any tool.

RULES YOU ARE HELD TO
  - A-334: no test double as evidence about an external system (Stryker,
    ciu provenance, vitest, go): committed real artifact + PROVENANCE entry,
    or a transcript in the REPORT, or the criterion stays `absent`.
  - A-335: `pytest tests/` green is not gate-green. You run the registered
    gate; the controller runs the release.
  - DESIGN-GUIDE §5: no invented defaults; cite a source for every convention.
  - decisions.md is APPEND-ONLY from A-408; backlog ids from B062; never
    allocate by hand what another branch may have taken — check main before
    filing and say so in the LOG.
  - File edits through the Edit tool (or apply_patch), never sed/python
    rewrite scripts (operator directive; Wave C generation 5 was written up
    for this).
  - Commit with `git commit -F <msgfile> --only -- <paths>`; prefixes
    feat/fix/test/docs/backlog(assay); the ONE `feat(assay)!:` on the cut;
    trailer lines:
      Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
      Claude-Session: https://claude.ai/code/session_01RJ3wqoyy8ZzHmj7ZK1qEnJ
  - CHANGES.md [Unreleased]: Added/Fixed/Changed/Documentation bullets per
    landed item, plus the "Migration notes (v9 → v10)" block once the cut
    lands.
  - Backlog hygiene: tick acceptance boxes ONLY with file:line evidence in
    the REPORT; note entries you add evidence to; mark RESOLVED with the
    A-row; file genuinely new findings, never fold them into an unrelated
    entry.
  - Every ruling above becomes an A-row when applied, naming the rejected
    alternatives from the entry.

GATE (run it yourself, from /workspaces/vbpub, AFTER the last commit of a
phase, and before every BRIEF)
  bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-d-v10
Run it detached from your tool call (`setsid nohup … > <log> 2>&1 &` with
your own `echo GATE_EXIT=$?` marker appended in the same shell), then read
the log in a SEPARATE step: one `ASSAY_REGISTERED_GATE_COMPLETE=1`, your
marker = 0, no FAILED|DIRTY_TREE|Traceback, the wheel name carrying the
judged commit. A harness "exit code 0" is not the job's status; the log
marker is. Commit before you gate (an untracked file is DIRTY_TREE). Paste
the transcript head/tail and the judged commit into the REPORT. Never claim
green on an earlier commit.

LOG / REPORT / BRIEF
  assay/nyxloom-trove/reports/assay-WAVE-D-v10-LOG.md — one entry per commit.
  assay/nyxloom-trove/reports/assay-WAVE-D-v10-REPORT.md — per item: every
  acceptance box with file:line evidence; every ruling's A-row; measured
  numbers (the B007 materialisation cost, the B052 normalisation cases, the
  ciu provenance documents captured); transcripts; the docs disposition
  table; decision asks; "what a reviewer should push on"; "what I did NOT do
  and why".
  assay/nyxloom-trove/reports/assay-WAVE-D-v10-BRIEF-<n>.md — the
  continuation brief at each checkpoint (cumulative delta only; BRIEF-1 is
  the seam map).

CHECKPOINT CLAUSE (E-008)
  ARM at ~120k context or ~60 tool calls (whichever first); CUT at the next
  coherent boundary (green gate > commit > LOG/REPORT write > edit-cluster
  end; never on a red gate): write BRIEF-<n> (what is DONE with hashes,
  what is NEXT as a literal task list, load-bearing file:line seams, the
  gate state, next free A-/B- ids) plus a self-authored retention prompt,
  commit, return. The controller dispatches a fresh successor seeded with
  the brief. Stop when fewer than ~40 calls of work remain.

BLOCKED
  If a ruling above cannot be applied as written, do not improvise a
  product call: implement everything that does not depend on it, write the
  exact question under "decision asks" in the REPORT, commit, and return.

Claim only what you ran — two fresh adversarial reviewers verify every claim
before the controller merges, and the controller runs the real release.
```

## Reviewer prompts

**R-1 (phase 1 tip):** the Wave A/C reviewer skeleton; blind pass on
`main...<phase-1 tip>` first. Push on: every B049 site with the fake tool
(and prove the SQL R2 and `mutation.py` folds are really covered, not just
the coverage read); B054's per-file split with a two-file istanbul artifact
(one defective outside the judged set, one inside); B053's stderr line
appears exactly once for every refusal class reachable through `assay run`
(enumerate them from `errors.py`) and the diagnostics stream carries the
same text; B028 via the installed CLI with a real slow command and a reserved
`--verdict-json` (the file exists and verifies); B029 with a real `derived:`
fact; B060's outcome test; the gate wiring actually fails the gate on a
planted unused import; nothing touched `verdict.py`/`verify.py`/the
schema/the drift-guard in phase 1; run the registered gate on the tip.

**R-2 (phase 2/3 tip):** blind pass on `<phase-1 tip>...<tip>`. Push on:
`verify.py` independence — every new field re-derived without importing
the model (B050's floor, B051's `discarded`, B007's aggregation, F015's
status); the drift-guard suite and `assay verify` refusing v9; B052 with
the real committed Stryker fixture mutated one file at a time (byte-identical
still passes; CRLF and trailing-newline variants pass; a rewritten function
refuses); B004 against the RE-CAPTURED ciu 7.10.1 documents (schema 2,
`unlabelled`), unknown status member refused, `verified_by_assay` cannot be
true on an adjudicated entry, the PASS oracle's provenance; B007's `any`
short-circuit bookkeeping, the 2N bound, `all` over a failing middle target,
and the B005 rule; F015 fail-before/pass-after with the test overlay (a test
that passes at the pre-fix commit must NOT yield PASS); the migration notes
against a real v9 lane file; run the registered gate on the tip.

Verdict format for both: ACCEPT / ACCEPT-conditional / NOT ACCEPT with
numbered blockers, file:line evidence, a prescription per blocker; product
calls are decision asks. On ACCEPT state it unambiguously — the controller
merges on your word. Round 3 is the cap; after it the controller stops and
reports to the operator.

## Release

`cmru release --project assay` from `/workspaces/vbpub` (detached, monitored;
push main fast-forward first; `--allow-uncommitted` for the stray
`assay/LAST_SUMMARY_CHECKPOINT`); expect **5.0.0** from the one `!` commit.
Deploy the wheel into `/home/vscode/.venv` sha256-verified; dstdns
release-notify with the migration actions that touch dstdns (its 3 SQL R2
lanes if any is R3 — check `assay.toml` — and `assay verify` refusing v9);
clear `[Unreleased]`.
