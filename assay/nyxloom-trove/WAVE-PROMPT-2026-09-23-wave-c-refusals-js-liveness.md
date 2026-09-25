# Wave prompt — Wave C: JS default-arg coverage, truthful refusals, liveness bounds, operator report (2026-09-23)

**Audience:** a third-party controller agent taking this wave end to end —
carve → implement → adversarial review → authoritative gate → merge →
release → deploy → notify. Written by the controller session that ran the
B101 wave hand-off. Everything decided is stated as decided; everything left
open is named as open, with who decides and a default.

**Scope, in order:** P0 (close-out reconciliation of assay-v7.0.0) → P1
(B080, JS default-arg branch coverage) → P2 (B081 + B094 + B025's last test:
refusals that tell the truth) → P3 (B095 + B076: liveness bounds) → P4 (B100,
`assay analyze report`; conditional, see 8) → P5 (B106, provenance-safe
selective mutation reuse; added by operator ruling 2026-09-25) → one release.
Do not fold in any other backlog entry without asking the operator (section 0
lists what is deliberately left out and why).

**Operator rulings added 2026-09-25:** B106 is approved as P5 after P0–P4;
verdict schema v13 is permitted if required for its evidence contract. A
reason-code or lane-schema change is also permitted after a GPT-6-Luna xhigh
recommendation and review, before the change is made. The 2026-09-25 GPT-6-Luna
xhigh B106 design review recommends v13 verdict fields only; it finds no need
for a lane-schema field or new reason code. The GPT-6-Luna xhigh follow-up
design review confirmed that boundary and identified the v12 hard-cut
conflict: recognize a bounded, parseable v12 JSON object as an unproven cold
start, do not validate or consume its candidate fields, and require a full
current run. Gate containers may run
in parallel when host resources allow. Use a unique CIU worktree and the
unique gate container name created by `run-gate.py`; no pre-gate RG-55 message
is required. Keep every gate in
`$CGROUP_PARENT_DEV_GATES` through the normal `run-gate.py` path. Host load may
inform capacity admission for starting parallel containers, but test outcomes
must not depend on host load or scheduling luck.

**Glossary** (assay-internal terms used below):
- **R0–R3** — rigor levels of a lane: R0 = the command's exit code; R1 =
  coverage judged; R2 = mutation testing; R3 = canary (a deliberate defect
  must be caught).
- **P22 snapshot** — the private per-run git repository assay materializes
  for R1+ lanes. Since v7.0.0 its seed is shallow by default.
- **Verdict** — the JSON document assay writes; `schema_version` is 12 as of
  v7.0.0 (`verdict.py` `VERDICT_SCHEMA_VERSION`). **Reason code** — the closed
  vocabulary in `errors.py` `ReasonCode`; adding one is a verdict-schema change.
- **istanbul / `default-arg`** — the JS coverage instrumenter's JSON format
  (`coverage-final.json`); a `default-arg` branch is one defaulted function
  parameter (`{ color = 'blue' }`), its count = how often the default applied.
- **`refuse_lane`** (`runner.py`) — the helper that writes a refusal verdict
  for a lane that cannot be judged. **`ProcessRunner`** — the process boundary
  every child command goes through.
- **Frozen generations** — the committed expected-verdict fixtures under
  `assay/nyxloom-trove/carve-assets/W*/` that the gate replays per schema version.
- **RG-55** — a separate, long-running run-gate/cgroup-profiler program run by
  another agent on this host. **cmru** — the release tool (`cmru release`).
- **dstdns** — the sibling product repo (`/workspaces/dstdns`) and assay's main
  external consumer; touched only through `.assay-inbox/release.json` (section
  9.1) and read-only measurement.
- **`assay verify`** (`verify.py`) — re-derives a verdict's claims from its own
  evidence and rejects one that does not re-derive; a refusal spelling only
  counts if verify accepts it (this bites in P2).

---

## 0. Why this wave, in one screen

| package | backlog | why now |
|---|---|---|
| P0 | close-out of v7.0.0 | the release left a stale/duplicated `CHANGES.md`, a stale backlog "open items" list, and two unfiled findings; cheap, do first |
| P1 | **B080** | the ONLY open item with live consumer pain: dstdns's JS lanes have hit it **six times in ten days across three assay versions** (D-423, D-429, D-433, B089 ×3); a fully-executed file is refused or dropped because istanbul puts a `default-arg` branch on a function-signature line. Any TS/React component with an optional defaulted prop triggers it |
| P2 | B081, B094, B025 (last box) | one theme — **a refusal must say what is true**: a wrong remedy in a `GIT_FAILED` message, a wrong reason code for a bad `--rejudge` id, an unguarded call site. All small, all local |
| P3 | B095, B076 | the two liveness leftovers deliberately deferred by RG-55's P7 (RW-53/RW-57): an unbounded per-second history list, and a recorded-but-unruled baseline gap |
| P4 | B100 | a designed, oracle-complete operator/agent tool (`assay analyze report`) that removes the terminal-scraping and polling that every long gate hand-off currently needs |
| P5 | **B106** | CMRU's 494-candidate, 4h09m50s campaign had to be repeated in full after judged inputs changed. Reuse must carry candidate identity and prove a current kill witness; uncertain candidates run again, and the final verdict must prove exhaustive coverage. |

Deliberately NOT in this wave (do not pick these up):
- **B103** (execution-interruption receipt, "P35") — design carved on unmerged
  branches, owned by the RG-55 continuation. Do not touch those branches.
- **B085, B087** (JS R3 canary) — need a real JS consumer to qualify against
  and, for B085, an operator ruling; ask dstdns first. **B078** checkpoints 2–3
  (pytest/go-test structured R0) — no consumer has been hit; revisit on a sighting.
- **Deferred by operator triage 2026-09-23:** B020, B023, B026, B064 (R3 half),
  B073, B086. (B079 was on that list but shipped in v12 — see P0.)
- **B105** (filed in P0) is a finding, not a package.
- **B106 is no longer deferred from this wave**; the operator added it as P5
  after P4 on 2026-09-25. No other backlog entry is added.

## 1. Where main stands (2026-09-23, `main` at `2f59fdd3`)

- **assay-v7.0.0 is released, installed in `/home/vscode/.venv`, and notified
  to dstdns** (zipapp SHA-256 `3e6f24bb…d638a5`). The wave that shipped it:
  shallow P22 seed by default, `snapshot_history = "full"` opt-in,
  `[isolation.limits]`, `dirty_ignore` + snapshot-only `--allow-dirty`, verdict
  **schema v12** (dirty provenance + the `discard_reason` split), liveness side
  files outside the checkout, B104. Log:
  `assay/nyxloom-trove/reports/assay-WAVE-B101-CONTROLLER-LOG.md`. Decisions
  A-451…A-455. **Next free decision id is A-456; next free backlog id is B105**
  (B103 is reserved for RG-55's P35; A-449/A-450 exist only on its unmerged branches).
- **Lessons from that release that correct the previous hand-off's assumptions:**
  1. Assay's release gate is `tester-unified` and took **1261 s (~21 min)** —
     NOT a multi-hour mutation campaign. Assay has no R2 lane over its own
     source (`assay/run-gate.toml` declares only `tester-unified`;
     `assay.toml`'s `[lanes.tester-unified]` is R0-only by A-046/A-133); cmru's
     release mutation gate and the CIU assay lane both found **no candidates**
     (`NO_MUTANTS`). So an assay release carries no mutation evidence for
     assay's own source. That is a finding (P0 files it), not something to hide.
  2. cmru's generated `## [7.0.0]` section in `assay/CHANGES.md` swallowed ~30
     unrelated commits, while the real narrative stayed in a hand-written
     `## [Unreleased]` block that cmru never folds: its
     `generate_release_changelog` (`cmru/src/cmru/changelog.py` ~285-294) only
     INSERTS a new generated section after `<!-- cmru: release history -->`.
     That block has been orphaned across 6.4.0, 6.5.0 and 7.0.0 — `assay
     analyze` in it actually shipped in **6.4.0** (commit `fde9527a` is in the
     `assay-v6.4.0` tag), not 7.0.0 (P0).
  3. The installed cmru (5.4.1) could not parse the current `[runtime]` config;
     the controller rebuilt it from source (now `cmru 5.4.2.dev262+ge434b293`).
     It also reported cmru's child remapper nesting a relative orchestration
     config under the candidate worktree (P0 verifies and files it).
- **Verdict schema starts at v12. P0–P4 do not bump it. P5 may bump to v13
  under the operator's 2026-09-25 ruling, but only for the B106 provenance
  contract.** A reason-code or lane-schema change requires the operator's
  further condition in the 2026-09-25 ruling (GPT-6-Luna xhigh recommendation
  and review; section 12).
- **The gate phase markers at this prompt's original HEAD were:** `wheel-installed`,
  `attestation-hardened`, `verdict-v5-accepted`,
  `lane-schema-v2-successors-verified`,
  `verdict-v6-v7-v8-v9-v10-hard-cut-verified`,
  `verdict-v12-successors-verified`,
  `judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
  `topos-qualified`, `cmru-b006a-qualified`, `independent-self-hosting-passed`,
  `pyflakes-clean` (verified by grep of `assay/tools/tester-unified-gate.sh`
  at `2f59fdd3`).

## 2. Read first (in this order)

1. `/workspaces/vbpub/AGENTS.md` — estate policies, worktree protocol,
   shared-main committing (`git commit --only -- <paths>`), "docs sync is
   MANDATORY" (~lines 191-235, A-270), "Consuming assay from inside vbpub"
   (~155-167). Binding.
2. `/workspaces/vbpub/CLAUDE.md` and `~/.claude/CLAUDE.md` — model routing,
   checkpoint/successor-brief rule, commit trailers.
3. `assay/nyxloom-trove/4-backlog.md` sections `## B080` (whole, including the
   2026-09-23 audit addendum), `## B089` (withdrawn duplicate of B080),
   `## B054` (the earlier istanbul fix this interacts with), `## B081`,
   `## B094`, `## B025`, `## B095`, `## B076`, `## B100`, plus the frontmatter
   index and "Open items at a glance". **The backlog sections are the
   requirements; this prompt sequences them and rules the forks.**
4. `assay/nyxloom-trove/reports/assay-BACKLOG-AUDIT-2026-09-23.md` (how the
   backlog was verified; ID-collision notes) and the B101 controller log above.
5. `decisions.md` rows **A-410** (B054), **A-342** (the JS adapter's
   "signature lines are legitimately unattributed" guarantee), **A-344**/B045
   (arc-bearing producers), **A-308** (B025), **A-415** (B028), **A-448**
   (`assay analyze`), **A-452…A-455** (the v12 decisions).
6. Code, by package: P1 `src/assay/coverage_parsers/coverage_istanbul_json.py`
   (whole file; `_contradictory_branch_lines`, `_without_lines`,
   `_branch_arcs`), `coverage_parsers/model.py` (`FileCoverage.__post_init__`,
   invariants ~408-445), `adapters/javascript.py` (~38-62), `evaluate.py`
   (where a file's `contradictory_branch_lines` becomes a by-name refusal),
   `evaluate.py` `_tally_branches` and its call (~581-599: arcs are tallied
   ONLY over changed lines already classified executed|missing),
   `runner.py` (where the same lines reach the diagnostics stream); P2
   `git.py` (`_resolve_repo` ~535-580, `_REPLACEMENT_ENV` ~125,
   `_linked_worktree_gap`, `_git_failed`), `mutation.py` (`MutationStateError`
   ~271-278, the rejudge refusal ~2252-2280), `runner.py` (`refuse_lane`;
   `_refuse_bad_rejudge_config` ~5953-6025 — the existing pre-flight
   `BAD_LANE_CONFIG` refusal for bad `--rejudge` input; the R2-fault path
   ~4497-4514 that turns an `AssayError` from `run_mutation` into an R2
   claim), `verify.py` (`_INDEPENDENT_R2_TERMINALS` ~2234 and the comment above
   it ~2214-2225), `cli.py` (`refuse_lane(` call sites
   ~1285/1374/1420/1510 and the `LANE_TIMEOUT` handlers ~1220-1510),
   `tests/test_cli_run.py` (~1805-1890, the attestation-timeout precedent);
   P3 `liveness.py` (~1300-1380, `cpu_samples`), `config.py`
   (`budget_per_candidate` ~437; `_refuse_unbounded_without_unit_bounds`
   ~2044, whose docstring says "B076: … deliberately not closed here"),
   `tests/test_config_unbounded_budget.py` (~585); P4 `analysis.py`
   (`inspect_verdict` ~298, `inspect_progress` ~323, `_tester_run` ~256,
   `build_analyze_parser` ~423, `cmd_analyze` ~457), `tests/test_analysis.py`.
7. Precedent prompts: `WAVE-PROMPT-2026-09-23-b101-isolation.md` (the previous
   wave — its sections 7.1–7.3 are still the process baseline; section 9 below
   corrects what changed) and `WAVE-PROMPT-2026-09-08-b074-b077-quickwins.md`
   (how small refusal/config quick wins were carved and reviewed).

## 3. Decision defaults (the operator may override before the package starts)

- **D1 scope/order** as in the header. P0–P3 and P5 are required; P4 is
  conditional, and P5 follows P4 or its explicit skip.
- **D2 B076 → option (a): leave the baseline unbounded, record the ruling,
  make the docs say so.** Reason: assay's settled rule is "assay does not
  watch itself"; the caller's stall detection (run-gate RG-36) covers the
  baseline and B064's `command_running` heartbeat makes a stalled baseline
  legible. Options (b) `judge.mutation.budget_per_baseline` and (c) overloading
  `budget` are recorded as rejected. If the operator picks (b)/(c) instead,
  first check whether an added lane key needs lane schema v3 — if so, stop.
- **D3 B094 → `BAD_LANE_CONFIG`, delivered so that `assay verify` accepts it.**
  `BAD_LANE_CONFIG` is the existing code for bad selection input (`--shard 9/2`,
  `runner.py` ~5896-5945; bad `--rejudge` shapes via `_refuse_bad_rejudge_config`
  ~5953). Exit code stays 2 (every `ERROR`). No new reason code. **Trap:** the
  unknown-id check (`mutation.py` ~2268-2280) runs INSIDE `run_mutation`, after
  the baseline passed; the runner turns its `AssayError` into an R2 claim with
  no mutation data (`runner.py` ~4497-4514), and `verify.py` accepts such a
  payload-free R2 claim only for reasons in `_INDEPENDENT_R2_TERMINALS` (~2234),
  which deliberately EXCLUDES `BAD_LANE_CONFIG` (comment ~2220: it is meant for
  `refuse_lane` verdicts, which carry the pair on every level). A `BAD_LANE_CONFIG`
  R2 claim next to a passing baseline is therefore rejected by verify.
  Ways out; pick one by reading the code, record it as decision row **A-458**
  (P2 runs before P3's A-457): (i) hoist the id check to before the baseline so
  it can use the existing `refuse_lane` path — NOT currently feasible: targets
  are resolved only inside `if r2_declared and result.outcome is Outcome.PASS`
  (`runner.py` ~4086) and candidate discovery / `selected_jobs` exist only
  inside `run_mutation` (`mutation.py` ~2168-2230), after the baseline check at
  ~2062; hoisting them moves discovery ahead of the ordering `verify.py`'s A-245
  comment (~2250-2256) relies on, which is itself a verify-policy change;
  (ii) extend verify's accepted set — a verification-policy change, so a
  stop-and-ask (section 12); (iii) catch the new exception subclass in the
  runner and refuse the WHOLE lane through the existing whole-lane refusal
  (`refuse_all` / `_refuse_lane_with_plan`, `runner.py` ~5208), which writes the
  same pair on every level and which verify already accepts — at the cost of
  discarding an already-measured R0/R1 result for what is an input error.
  (iii) is the smallest change; weigh that cost and record it. The same
  exception also refuses R3 (`runner.py` ~4553-4570), so the verify oracle needs
  an R3-declared lane or an explicit statement that R3 is excluded. Either way,
  an oracle must run `assay verify` over the CLI-produced refusal verdict and
  see it accepted.
- **D4 B081 → message + docs only.** Assay must NOT start passing
  `-c safe.directory=…` to git: command-line config is protected scope, so it
  would work, and that is exactly why it is a security decision (it would turn
  off git's ownership check for every judged repo). Ask the operator if you
  think it is warranted.
- **D5 B080 shape is the carver's call** (constraints in P1) — but it is
  recorded as a decision row, with the rejected shapes named.
- **D6:** P0–P4 make no verdict-schema, lane-schema, or reason-code change.
  P5 may bump the verdict schema to v13 for B106 under the 2026-09-25 ruling.
  Before any P5 reason-code or lane-schema change, obtain the requested
  GPT-6-Luna xhigh recommendation and review and record it in the P5 report.

## 4. P0 — close-out reconciliation (do first; docs/records only, no product code)

1. **`assay/CHANGES.md`.** At `2f59fdd3` there is a hand-written
   `## [Unreleased]` block (lines 5–56; line 57 is cmru's
   `<!-- cmru: release history -->` marker and MUST stay — cmru refuses a
   release without it) and cmru-generated sections below it, the newest
   `## [7.0.0] - 2026-09-23` at line 59. cmru never folds `[Unreleased]`
   (section 1, lesson 2), so the block has sat orphaned across three releases.
   Fold it by hand: the `assay analyze` entry belongs in the `## [6.4.0]`
   section (verify with `git show assay-v6.4.0:assay/CHANGES.md` and the
   commit `fde9527a`); the B101/B102/B093/B079/B104/docs entries belong in
   `## [7.0.0]` (place them under their own subheadings, outside cmru's
   `<!-- cmru: … -->` markers). Leave `## [Unreleased]` present and empty.
   Do not edit the already-published GitHub release notes.
2. **`4-backlog.md` "Open items at a glance" and the entries' BODY status
   lines.** (The backlog frontmatter has no status field and its schema
   forbids extra keys — see the audit report's frontmatter section; never
   add one.) The list still says B101/B102/B093 are "DONE in the B101 wave
   worktree; provisional merge follows review", and lists **B079 as
   DEFERRED** — B079's body status line is stale too — although it shipped in
   v12 (`verdict.py` `discard_reason`, A-454). Everything from that wave
   (B101, B102, B093, B104, B082, B083, B084, B079) becomes DONE with
   `assay-v7.0.0` + commit evidence, and the "Wave B101 isolation (next)"
   heading becomes "shipped". Add this wave's packages under a "Wave C"
   heading. B105 (next item) also needs its frontmatter index entry
   (`{id, title, type, component[, context_estimate]}` like its neighbours);
   validate the frontmatter the way the audit report describes.
3. **File B105** — "assay's own source has no R2 lane; a release carries no
   mutation evidence for assay itself" — with the evidence in section 1 (the
   controller log's "Release and final closeout"). Options for the record, not
   for this wave: declare a full-source R2 lane (hours of campaign; RG-55
   territory) or accept and document. Note that the previous hand-off's claim
   of a multi-hour release mutation campaign was wrong for assay.
4. **cmru findings go in cmru's own backlog**
   (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md`, last entries KI-28/KI-29; convention:
   a finding about a TOOL is filed in the tool's backlog, never worked around
   locally). (a) The hand-written `## [Unreleased]` block is never folded:
   KI-23 covers only the `## [X.Y.Z] - UNRELEASED` form
   (`changelog.py` ~36 is the regex; the refusal is ~256-262 and fires when
   that heading's version equals the version being released), so
   file **KI-30** with the three-release recurrence (6.4.0/6.5.0/7.0.0) as
   evidence — as a question ("should cmru fold `## [Unreleased]`?"), not a
   patch. (b) The child-remapper nesting failure: first check for retained
   release worktrees (`cmru worktrees`; the shared checkout already holds many
   old `cmru-release-*` ones, and a `--dry-run` takes the release lock and
   creates a transaction worktree — clean yours up with cmru's own
   `--abandon`), then try **one** `cmru release assay --dry-run` on the
   rebuilt cmru; if the nesting failure reproduces, file **KI-31** with the
   exact command, the error and the `--config` absolute-path workaround the
   previous controller used; if not, record "not reproduced on 5.4.2.dev262"
   in the controller log. Do not fix cmru in this wave.
5. **Housekeeping facts, not actions:** four merged, clean worktrees
   (`assay-b088-resume-identity`, `assay-b092-b098`, `assay-b097`,
   `assay-liveness`) belong to the RG-55 program's close-out (its hand-off
   names `assay-liveness` for teardown). Do not remove them; tell the RG-55
   controller they are clean and merged.
6. Start the controller log
   `assay/nyxloom-trove/reports/assay-WAVE-C-CONTROLLER-LOG.md`.

P0 still gets a fresh adversarial review (docs included; size changes how
heavy, never whether) and the gate before merge like any package — a docs-only
gate run is cheap relative to the rule it protects.

## 5. P1 — B080: istanbul `default-arg` branch on a signature line

### 5.1 What is broken (measured)

istanbul's `statementMap` has no statement for a function's own signature line,
and `adapters/javascript.py` (~38-62, A-342) documents that as legitimate. But
istanbul places every `default-arg` branch node on the parameter's source line —
often that same signature region. `FileCoverage.__post_init__`
(`model.py` ~426-431) then raises "branch line(s) … in neither .executed nor
.missing", and the istanbul parser's B054 isolation
(`_contradictory_branch_lines`, `coverage_istanbul_json.py` ~352-390) DROPS the
arc and records the line, so `evaluate` refuses by name if that file is judged
and `runner` names it in diagnostics if not (that is B089's "drop-and-continue"
face). Both faces are the same defect.

**Real specimen, measured 2026-09-23 (read-only):**
`/workspaces/dstdns/applications/webapp-ui-react/.assay/coverage-final.json`
(gitignored artifact, may vanish): 35 files, 7 `default-arg` branches, **6 on
lines that carry no statement** — `ChartCard.tsx:34`, `DataTable.tsx:33/34/35`,
`StatCard.tsx:17`, `StatTile.tsx:28` — all with non-zero counts. Copy a
minimized fixture into assay's tests; **no test may read a dstdns path**.

### 5.2 What the filing does not say (you must resolve it, not inherit it)

- **The zero-count hole.** The backlog's preferred "Shape A" accepts a
  signature-line branch only when its covered-arc count is non-zero, and its
  oracle 2 pins "count `[0]` → still refuses". But an istanbul `default-arg`
  count is *how often the default was applied*; a default that no test
  exercises has count 0 on a function that ran fine — the equally ordinary
  case. The real artifact happens to contain none, nothing prevents one. A fix
  that handles only non-zero counts would leave part of the population failing.
- **Two constraints that rule out the obvious fixes** (reviewer-verified):
  1. *Merely tolerating a branch line in neither bucket does not work.*
     `evaluate.py` (~581-599) tallies arcs ONLY over changed lines already
     classified executed|missing, so an unclassified line's arc is never
     counted — the fix has to CLASSIFY the line (or state that the arc is
     deliberately uncounted).
  2. *The rule must key on the branch node's `type` (`default-arg`), not on "a
     line in neither bucket".* B054's braceless-`if` witness is also a
     neither-bucket, zero-count branch line; a type-blind rule changes B054's
     ruling (a stop-and-ask, section 12). The type exists only inside the
     parser (`_branch_arcs`/`_entry_arcs`); `BranchCoverage.by_line` carries no
     types, so this is a parser-level fix (the filing's "Shape B" territory),
     not a `FileCoverage` invariant relaxation ("Shape A"). Also do not
     classify a signature line `missing` merely because its default count is 0 —
     the function ran; that would report an executed line as unexecuted.
  Candidate rules, decide by measurement on the real artifact and record the
  rejected ones: **(C)** classify the signature line from the enclosing
  function's own call count (`fnMap`/`f` — **currently never read**, parser
  docstring ~line 57, so consuming them is a parser-contract change that needs
  docs; check that a `default-arg` node can be mapped to its function
  reliably — measured 2026-09-23 on the real artifact: 0 of the 6 lines fall
  inside any `fnMap` `loc` (it starts at the function BODY, e.g. ChartCard
  `34:104`), 3 of 6 match on line alone, but **6 of 6 map to exactly one
  function with `decl.start ≤ node < loc.start`**; and a default's count is not
  its function's call count (`f` = 9/6/6/6/18/26 vs `b` = 9/6/6/6/15/7), so
  `b` cannot stand in for `f`); **(B′)** count>0 ⇒ line executed (the default applied, so the
  function ran); count 0 ⇒ the arc is dropped with a NAMED, non-refusing
  diagnostic, an explicit documented gap; **(D)** drop every `default-arg` arc
  on a statement-less line, named, never counted. Whatever you pick: no
  fully-executed file is refused or dropped-as-contradictory, the numbers are
  asserted, and any change to executable-line denominators (C classifies extra
  lines) is quantified on the real artifact and stated.
- **Interaction with B054.** B054 (A-410) deliberately drops arcs on a line in
  neither bucket for a braceless single-statement `if`. It stays UNCHANGED
  (separate ruling, by-name refusal kept); record that. Changing B054's ruling
  is a stop and ask (section 12).
- **Which previously-PASS verdicts change.** Argue and test that no lane that
  passed before changes its number; state what changes for JS consumers (files
  that were dropped or refused are now judged, including in `whole_target`
  lanes that name them) in the release notes and the dstdns notify.

### 5.3 Oracles (acceptance)

1. The real-shaped fixture (default-arg branch on a statement-less line,
   non-zero count) parses, the line is classified per the chosen rule, and the
   arc is **counted** by `require_branch` — assert the numbers (or, under
   rule (D), assert the deliberate exclusion and the named diagnostic).
2. The zero-count variants, each pinned with its reasoning in the docstring:
   default never applied on a function that ran (`f > 0`, `b == [0]`), and a
   function that never ran (`f == 0`).
3. `tampered_missing` still fires independently (a line explicitly in
   `.missing` carrying a non-zero arc still raises). This oracle must stay
   GREEN under mutant M1 below — that is what proves the two checks are
   independent.
4. **Controlled wrong implementations** (each must turn the named oracle RED;
   if not, the fix deleted an integrity check instead of narrowing it):
   **M1** = "tolerate ANY neither-bucket branch line": remove `FileCoverage`
   invariant 3 (`model.py` ~424-430) AND the `unconsidered` half of
   `_contradictory_branch_lines` together (either alone does not do it: the
   parser drops those lines before the model sees them, and an empty
   `_contradictory_branch_lines` alone makes the model raise and refuse the whole
   artifact). Under M1 the B054 braceless-`if` witness goes RED, and the
   default-arg fixtures go RED wherever they assert classification or counts
   (the line stays unclassified and its arc uncounted, `evaluate.py` ~593);
   oracle 3 stays GREEN. Pin oracle 3 at the `FileCoverage` level — at parser
   level a tampered line is currently isolated, not raised. **M2** = "apply the
   new rule to ANY neither-bucket branch line regardless of node type" → the
   B054 witness goes RED. The committed B054 fixture carries `b = [1, 0]`
   (`tests/test_coverage_istanbul_contradictory_branch_arcs.py` ~80), so also
   add a B054-shaped witness whose neither-bucket branch has a ZERO count —
   the field witness is zero-count, and a type-blind rule that touches only
   zero-count lines would otherwise slip through green.
5. End to end through the real CLI: a `javascript` lane declaring an
   arc-bearing `judge.coverage.producer` (the istanbul parser reads no arcs
   otherwise, `coverage_istanbul_json.py` ~246), `require_branch = true`,
   `mode = "changed_lines"`, over a file with a defaulted destructured
   parameter produces a real PASS/FAIL verdict with a branch number, not
   `ERROR`/`UNREADABLE_ARTIFACT` and not a by-name refusal.
6. Both faces: a default-arg file inside the judged diff (was: by-name
   refusal) and outside it (was: dropped with a diagnostic) — neither is
   reported as contradictory afterwards; B054's braceless-`if` witness
   behaves as ruled.
7. Docs and adapter guarantee stay true: if attribution changes,
   `javascript.py`'s A-342 sentence and DESIGN-GUIDE §11 change with it.

### 5.4 Records
Decision row **A-456** (chosen shape, rejected shapes, zero-count ruling,
B054 disposition); B080 DONE (+ B089 note); docs in the SAME package:
DESIGN-GUIDE §11, `javascript.py` docstring, `coverage_istanbul_json.py`
docstring, `docs/CONSUMERS.md` JS section, `CHANGES.md`.

## 6. P2 — refusals that tell the truth: B081, B094, B025's last box

### 6.1 B081 — dubious-ownership `GIT_FAILED`
`_resolve_repo` (`git.py` ~535-580) surfaces git's own text, which tells the
consumer to run `git config --global --add safe.directory <path>`. That can
never work under assay: `_REPLACEMENT_ENV` (~125) replaces the child
environment and sets `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_SYSTEM`/
`GIT_CONFIG_GLOBAL=/dev/null`, no `HOME`/`XDG_*`. Follow B068's discipline
(`_linked_worktree_gap`): the backlog's acceptance (`## B081`, "Acceptance")
is exact and binding — an assay-composed sentence FIRST naming the ownership
mismatch and that `safe.directory` is unreachable under assay's replacement
environment, then git's own `fatal:` line (only that line: git's following
"To add an exception … call: git config --global --add safe.directory …"
lines must NOT be carried, because the message must not propose that remedy
anywhere). The only working remedies are running as the repository's owner or
fixing the tree's ownership/uid mapping. B068's linked-worktree message still
wins where it applies; a healthy resolution never consults the new probe; an
unrecognised failure passes through unchanged. Do not add a `safe.directory`
bypass (D4). Test path: the function takes a `git_executable`; a fake
executable that prints git's real dubious-ownership stderr and exits 128 is
hermetic (capture git's genuine text once with
`GIT_TEST_ASSUME_DIFFERENT_OWNER=1` outside assay's replaced env — it exists in
git 2.55 on this host), plus a check that an ordinary bootstrap failure keeps
its old message. Docs, all three (AGENTS.md docs-sync): `README.md`,
`docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md` (pitfall entry).

### 6.2 B094 — unknown `--rejudge` id reports the wrong reason
`mutation.py` ~2268-2280 raises `MutationStateError` (whose reason is fixed to
`UNREADABLE_ARTIFACT`, ~271-278) for an id that is not in the current candidate
set (unknown, or its source bytes changed). That is bad user input, not a
corrupt store. Give the raise its own classification without relabelling real
state errors (a distinct exception subclass or an explicit per-raise reason;
D3: `BAD_LANE_CONFIG` — **read D3's verify trap first**: the check runs after
the baseline passed, and `verify.py` rejects a `BAD_LANE_CONFIG` R2 claim
there). The existing pre-flight `_refuse_bad_rejudge_config` (`runner.py`
~5953) is the model for a refusal verify already accepts. Oracle (from the
backlog): unknown and stale-source ids refuse before any record replay with
the input-refusal code; unreadable / corrupt stores keep the artifact-error
codes; valid ids still rejudge only their selected records; drive it through
the real CLI, assert exit code and reason, **and run `assay verify` over the
produced verdict and see it accepted**. Leave the other `MutationStateError`
raises (shard merging, ~1444/1460 — B023, deferred) alone. Sync docs where
the reason vocabulary or the `--rejudge` behavior is described (README,
DESIGN-GUIDE, CONSUMERS).

### 6.3 B025 — the one unmet acceptance box
Everything else in B025 shipped (A-308, four crash sites). The open box: the
`cli.py` attestation-`LANE_TIMEOUT` `refuse_lane` call forwards
`infrastructure_source`/`infrastructure_environment` by inspection only —
reverting just that kwarg leaves the whole suite green. Write the test that
fails when that forward is removed (red first: revert, watch it go red,
restore). Do NOT reuse the B028/A-415 trigger (a real tiny budget plus a
sleeping command): an expired budget of that kind is caught earlier, at
`head_rev` (`cli.py` ~1285), and never reaches the attestation site. The
precedent that does reach it is `tests/test_cli_run.py` ~1805-1890
(`test_an_attestation_timeout_outranks_an_adapter_that_would_refuse`), which
`monkeypatch`es `git.verify_exact_commit` to raise `LANE_TIMEOUT`; copy that
shape, add a lane with a resolvable `[lanes.*.infrastructure]` `derived:` fact
(pattern at `tests/test_cli_run.py` ~1356) so the forwarded kwargs matter,
assert the refusal verdict carries the resolved infrastructure environment,
and confirm it goes red when the kwargs at the attestation `refuse_lane` call
(`cli.py` ~1374-1385) are reverted. Then close B025 (status line +
at-a-glance).

## 7. P3 — liveness bounds: B095 and B076

### 7.1 B095 — the monitor's unbounded history
`liveness.py` (~1312) keeps every one-second sample in `cpu_samples`
(appended ~1352, scanned newest-first ~1364), for every candidate including
unbounded ones, and rescans event/proc data each tick. Bound the history
while keeping exactly the sample the trailing window needs at its edge.
Oracle (from the backlog): a long virtual run retains bounded history while
CPU-window classifications, partial-line tolerance, process-tree accounting and
`/proc`-failure behavior stay **exactly** as before (differential: run the old
and new logic over the same recorded event/proc sequence and assert identical
classifications). Measure before/after cost on a large events file and record
the numbers in the controller log; no timing threshold replaces the behavioral
checks. Assess incremental event parsing separately and only take it if the
measurement justifies the risk in the loop that B1/B2/B6 just hardened.

### 7.2 B076 — the baseline gap (default: option (a), D2)
The pinned fact: on an `unbounded` R2 lane the baseline runs with
`timeout=None` (`tests/test_config_unbounded_budget.py::
test_a_real_unbounded_R2_lane_runs_every_candidate_with_no_lane_timeout`
records `[None, 45.0]`). Record decision **A-457** naming the ruling and the
two rejected options with their reasons, verify (and if needed add) the plain
statement in CONSUMERS/DESIGN-GUIDE that an unbounded lane's baseline is
watched only by the caller and visible through the `command_running`
heartbeat, keep the pinning test with its comment updated to cite the
ruling, and close B076 as RULED. No behavior change under (a) — but the
docstring of `_refuse_unbounded_without_unit_bounds` (`config.py` ~2044) and
the comment near `config.py`'s per-unit key list still say "B076: filed,
reasoned, and deliberately not closed here"; they must be updated to point at
the ruling.

## 8. P4 — B100: `assay analyze report` (conditional)

Start only when P0–P3 are merged and gated. If it is not merged by the P5
start boundary, skip it and leave B100 OPEN; P5 still proceeds before release.

**Requirements are `## B100` in the backlog** (a read-only, commit-bound,
bounded, non-polling snapshot command over explicit verdict / progress / log
paths; `status` ∈ running/pass/fail/evidence_error; distinct exit codes; text
and JSON; `--max-errors` with a small validated upper bound; never writes into
the judged tree; never derives a status from a path's existence or a child log
line). Build it on `analysis.py` and follow the existing `analyze
verdict`/`progress` option style rather than inventing spellings. **Three
existing behaviors block a naive reuse — carve around them, do not paper over
them:** (1) `inspect_progress` (~323) JSON-parses EVERY line and raises on a
malformed one, so B100's "running stream with a partial final line" oracle
fails against it; (2) `inspect_verdict` (~298) raises when a verdict records
`overridden_dirty_paths` (the v12 release-receipt refusal, ~306-311), whereas a
report should SHOW such a verdict, not refuse to read it; (3) `cmd_analyze`
(~457-517) returns 1 for every error, but B100 needs distinct exit codes. The
default is: add new non-refusing readers for `report` and give it its own exit
mapping, leaving `analyze verdict`/`progress`/`receipt` behavior byte-for-byte
unchanged. Changing an existing subcommand's behavior is a stop-and-ask
(section 12). The ten oracle cases in B100 are the acceptance list
(pass, fail, running stream with a partial final line, stale/no-terminal
stream, missing path, malformed JSON, commit mismatch, multiple lanes, bounded
error selection, over-limit log) — each asserts JSON shape, text shape and exit
code; add one that proves the existing subcommands' behavior is unchanged.
Docs in the SAME package: README `assay analyze` section, DESIGN-GUIDE,
CONSUMERS, `docs/INTERNAL-CONSUMERS.md`. **Do not edit `AGENTS.md`** (estate-
shared); put the suggested "controllers call `assay analyze report` after a
long gate" wording in the controller log for the operator.

## 8.1. P5 — B106: provenance-safe selective mutation reuse

Start after P4 is merged and gated, or after P4 is explicitly skipped. P5 is
in scope under the operator's 2026-09-25 ruling; do not release before it is
complete. A verdict schema v13 is permitted for this package. A reason-code or
lane-schema change is permitted only after the GPT-6-Luna xhigh recommendation
and review required by the operator, recorded before implementation. That
review recommends no such additional change. Its follow-up review also
confirmed the candidate identity inputs, inventory scope for shards, and the
requirement that a replay receipt agree with pytest's ordinary exit code.

**Contract:** reuse must be based on current evidence, not candidate bytes
alone. Add `assay plan <lane> --reuse-from <verdict>` as a read-only preview
and `assay run <lane> --reuse-from <verdict>` as the execution option. Read one
prior artifact with a bounded, duplicate-key-rejecting JSON parser. A v13
artifact is inspected only after it passes the current verifier. A v12 JSON
object is the sole cold-start exception: recognize `schema_version = 12`,
classify it as unproven, and do not validate or consume any of its candidate
fields. This does not make v12 valid under `assay verify`; it preserves the
v13 hard cut while making an old artifact a safe full-run starting point.
Every other foreign version, unreadable path, malformed JSON, duplicate key,
or invalid v13 artifact is a preflight `UNREADABLE_ARTIFACT` refusal.
Classify current candidates as (a) eligible for a current witness replay, (b) prior
outcomes that require a full current run, including survivors, crashes, hangs,
budget exhaustion, equivalence and missing witnesses, (c) current candidates
absent from the prior plan, or (d) prior candidate IDs no longer in the current
plan. A complete source verdict may have an overall R2 FAIL because candidates
survived; its individually verified killed outcomes can still supply witnesses.
The run must always execute the current R0 baseline first and require PASS
before any candidate replay. After baseline PASS, `run` rediscovers the full
current candidate inventory from its own snapshot; the `plan` preview is an
estimate, not a certificate of what execution covered. `--reuse-from` with
any shard selection is a preflight `BAD_LANE_CONFIG` refusal; selective reuse
produces only a complete unsharded campaign.

**Chosen safe granularity:** a v13 full sequential pytest mutation run records
the first call-phase failing pytest node ID in final collection order for each
killed candidate when that test is observable.
On a later run, for an identical candidate ID, assay may replay the current
pytest suite up to that prior witness. Preserve the full collected item list
and its final order; do not deselect later items or alter collection, so
session fixtures and plugins see the same collection and all setup before the
witness executes. The witness node must occur exactly once. Stop the ordinary
sequential runner only after the witness item completes; a report for that exact
node must show a call-phase failure, pytest's session exit status and the
child process exit status must both be the ordinary test-failure status (`1`),
and those facts must agree in a bounded receipt to certify a current kill. An earlier node failure
or a setup/teardown error alone is insufficient. A PASS, skip, missing witness,
malformed/oversized receipt, absent or duplicate node ID, unsupported pytest
command, xdist or custom test-loop execution, inconsistent exit status, or any
other uncertain result triggers a separate full-suite execution in a fresh
process through the ordinary candidate lifecycle, including a snapshot freshly
materialized from the pristine current candidate input, new output paths, and
any ordinary resets for shared or linked resources. Never carry forward a
`survived`, `crashed`, `hung`,
`budget_exceeded`, or `equivalent` outcome. New candidates and prior survivors
always get a full run. The current run records per candidate whether it ran
fully or was killed by a current witness-prefix replay, including the prior
verdict digest, prior node ID, current node ID, and receipt of the current
failed report. The previous artifact is an input to choose a replay point,
never evidence for the current kill.

**Compatibility and limits:** a v12 artifact has no complete candidate-ID
inventory or test-level kill witness, so it proves no reusable candidate.
When supplied for preview, classify the evidence as unproven and show that
every current candidate will run fully; when supplied for execution, report
this fallback before continuing. `assay verify` still rejects v12 under the
hard cut; the `--reuse-from` cold-start path reads only the bounded JSON
envelope and its exact version. A valid but incomplete v13 source verdict,
including a shard, is unproven and falls back to a full run. Initially support
only a direct `pytest` executable
command or the lane's Python executable invoked with `-m pytest`; shell and
tool wrappers are unsupported. Capture/replay is allowed only in sequential
mode with pytest's ordinary runtest loop and protocol; xdist, a custom loop,
or uncertain plugin behavior falls back to full execution. A replay that
reaches the witness only after most of the suite may save little
time; the feature promises correctness and records the measured work, not a
particular speedup.

**Completeness:** schema v13 reuses `mutation.candidate_ids` as the exhaustive
inventory for the verdict's candidate scope. For a complete unsharded run it
is the full current plan; for a shard it is the selected slice. An empty array
means an empty submitted scope (a genuine zero-candidate run or empty shard).
A pre-submission `MUTANT_LIMIT_EXCEEDED` sentinel omits this inventory because
no candidate scope was submitted; its existing `candidate_count =
max_mutants + 1`, `total = 0` shape never qualifies as a complete or reusable
campaign. For completed native scopes, the inventory must equal the
outcome-ID set, including the empty case, and shard metadata prevents a slice
from claiming campaign completeness. Only a full unsharded campaign can be
a B106 reuse source. Every native outcome carries its `candidate_id`,
`source_sha256`, and `mutated_file_sha256` alongside the existing path,
operator, span and `replacement_sha256`. These fields bind each ID to
sufficient inputs to recalculate it through one pure canonical candidate-ID
helper shared by planning, execution and verify: canonical relative path,
operator, SHA-256 of the original UTF-8 source-file bytes, exact start/end
byte span, and SHA-256 of the entire mutated file. `verify` imports only that
leaf helper and still independently checks native-only requiredness, shapes
and cross-object coverage. Ingested outcomes omit all B106 fields.

Closed per-item execution provenance records `full` or `witness-prefix`. A
full run may include a call-phase failure witness only on a killed outcome
when the pytest session and child process both exit 1. A witness-prefix run
must be killed and include the SHA-256 of the exact prior verdict bytes, prior
witness node ID, matching current node ID, and the current failed call-phase
receipt with both exit statuses equal to 1. A receipt contains only the node
ID, `when = "call"`, `outcome = "failed"`, and the two exit statuses; each
node ID is bounded to 4096 UTF-8 bytes, and arbitrary output/tracebacks are
never recorded. `assay verify`
requires inventory/outcome sets to be equal, duplicate-free, disjoint across
outcome buckets, structurally valid for the recorded execution mode, and each
candidate ID to match its recorded identity inputs. The producer refuses to
claim a complete run when any candidate has neither a fresh full outcome nor
a valid current replay.
An ordinary `--resume` continues to require its exact judge identity;
`--rejudge` overrides selective replay and forces a full run for the named
candidate. A survivor-only or sharded run is never a complete campaign.

**Required oracles:** a parseable v12 prior artifact is a cold start that
yields no reusable outcomes while `assay verify` still rejects it; malformed
and duplicate-key inputs refuse; a v13
complete pytest campaign with killed, surviving, and timed-out candidates
classifies each safely; changed candidate bytes become new work; changed tests
are exercised under the current command; replay failure is accepted only when
the designated current node's call-phase report fails after current baseline
PASS; witness pass, deleted test, fake/oversized receipt, duplicate/missing
IDs, mutated prior outcome, changed source or mutated-file digest, mismatched
session/process exit statuses, the pre-submission mutant-limit sentinel, and
unsupported/xdist command all cause full execution or refusal; resume/rejudge
retain their current contracts; a complete verdict passes `assay verify` and
the registered gate while missing, duplicate, stale, or unproven coverage does
not. The prior-verdict digest is an audit reference, not a signature; only the
fresh current baseline and candidate execution establish a current result.
Docs in the same package: README feature and
DESIGN-GUIDE rationale, plus a pasteable CONSUMERS example showing full-run
witness capture and a later `--reuse-from` run. The docs must state the v12
cold-start and unsupported-command full-run behavior.

## 9. Process — how to run it

Sections 7.1 (worktrees/branches), 7.2 (roles) and 7.3 (host rules) of
`WAVE-PROMPT-2026-09-23-b101-isolation.md` apply unchanged — read them. The
essentials, and what changed:

- **Worktrees:** create one unique `ciu worktree` per package under
  `/workspaces/vbpub/.worktrees/<branch>` from current `main`, merged serially
  `--no-ff` (`git merge --no-ff -F <msgfile> <branch>` after checking
  `git status`); suggested branches
  `assay-wave-c-p0-closeout`, `assay-b080-js-default-arg`,
  `assay-refusals-b081-b094-b025`, `assay-liveness-b095-b076`,
  `assay-b100-analyze-report`, `assay-wave-c-p5-b106-selective-reuse`.
  Direct commits in the shared checkout only with
  `git commit --only -- <paths>`. Never bare `git stash`. Edit files with the
  Edit tool / apply_patch, never sed. Remove each worktree and branch once
  merged. After every agent returns, check `git status` of its worktree before
  trusting its tip (an agent's own sub-agents once left an index staged back to
  an old commit).
- **Roles:** you = strongest available model, long-lived, writes the carve for
  P1 and P4 (a short design doc in `handoffs/` or the backlog section) and the
  controller log. Implementers fresh (Opus for P1/P4, Sonnet acceptable for
  P0/P2/P3 mechanics). Reviewers: fresh Opus, **never a fork**, adversarial,
  3-round cap, one per merged change including docs. Checkpoint rule: arm at
  ~120k context or ~60 tool calls, cut at the next green boundary with a
  continuation brief committed to the worktree; successors are fresh.
- **Host rules** (this host also runs a production game server;
  `ptero-wings-*` is up): pytest serial under `nice -n19 ionice -c3`,
  `PYTHONPATH=src`, from `assay/`; no `-n auto`; keep
  `--deselect tests/test_gate_qualify_dstdns_sql.py` in plain local runs (it
  is Docker-reaching; the previous wave's full local run was 4799 passed,
  19 skipped, 95 deselected). Parallel gates are allowed when host CPU and
  memory capacity permit. Every gate runs from its unique CIU worktree through
  `./run-gate.py tester-unified`; run-gate places it under
  `$CGROUP_PARENT_DEV_GATES` and emits a unique container name. Cap each exact
  container with `docker update --cpus=3 <exact container name>` and remove
  containers by exact name only. Do not wait on, contact, or serialize against
  RG-55 for an assay gate. CIU's stale RG-55 worktree metadata is not permission
  to edit any `rg55-*` worktree. Keep test assertions independent of host load
  and thread scheduling; use protocol synchronization instead of sleeps or
  incidental timing.
- **Gate (per package, before merge)** — 60-min budget, run from its worktree:
  `./run-gate.py tester-unified`. Read `GATE_EXIT`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, and every expected
  `ASSAY_GATE_PHASE=` marker from the completed run log. P0–P4 require the 12
  markers recorded at their merged tree; P5 also requires
  `verdict-v13-successors-verified`. A bare `pytest tests/` pass is NOT
  gate-verified.

### 9.1 Release, deploy, notify ("shipped" = all of these)
- Standing authorization: merge, push and `cmru release` in vbpub without
  asking once review + a real gate are green.
- One release for the whole wave. `cmru release assay` from
  `/workspaces/vbpub` (the project is positional; no `--project` flag). Push
  `main` to origin FIRST (the release snapshots `origin/main`). Run
  `--dry-run` first and read the planned version from the final commits. Do not
  force a major/minor/patch override: a v13 compatibility change may affect
  semver, so inspect cmru's decision and ensure the commit classification
  matches the shipped contract. Never
  `--resume` without re-checking the version (the nyxloom v1.0.0 incident).
  Run it detached (`setsid nohup bash -c '{ cmru release …; echo
  CMRU_RELEASE_EXIT=$?; } > log 2>&1' &`) and read the exit marker. The gate
  inside the release is the ~21-min `tester-unified` run — no mutation
  campaign for assay (section 1) — so a long host window is not needed, but
  the section-9 load check still is. After the release cmru may WARN that it
  could not sync local `main` because the shared checkout is dirty: then
  `git merge --ff-only origin/main` by hand once the tree is clean. If cmru
  cannot parse the current config, bootstrap it
  (`cmru/build-initial-standalone.sh` + `pip install --no-deps
  --force-reinstall` of the built wheel).
- **No cmru assay pin to bump** (AGENTS.md "Consuming assay from inside
  vbpub"; `cmru/cmru.toml` ~19-21). Out-of-repo pins (dstdns
  `assay_command = tools/assay/assay-…pyz`) are updated by that consumer.
- Deploy: `/home/vscode/.venv/bin/python -m pip install --upgrade <released
  wheel>`; verify `pip show assay` and `/home/vscode/.venv/bin/assay --version`
  report the released version. Exactly one agent installs — tell RG-55.
- Notify dstdns: write `/workspaces/dstdns/.assay-inbox/release.json` per that
  directory's tracked `CONTRACT.md` (read it at the time; `sha256` from the
  release's own `.sha256` sidecar; `landed` lists what actually shipped —
  `B080` and `B089` are dstdns-filed). Notes: one line each for the JS
  default-arg fix (files dstdns had to treat as broken are now judged; branch
  numbers for them appear for the first time — say so plainly) and anything else
  a consumer can observe. Read dstdns's decisions D-423/D-429/D-433
  (read-only) to see what it did about the defect and say what it can undo.
- **`CHANGES.md` after the release:** during the wave, hand-written entries
  live in `## [Unreleased]` (docs-sync); cmru will insert its generated
  version section BELOW the history marker and will NOT fold them. After
  the release, MOVE (do not delete) the hand-written entries into the new
  generated section and leave `## [Unreleased]` empty — a small docs commit,
  reviewed like any other. Never put the entries under
  `## [7.1.0] - UNRELEASED` beforehand: cmru refuses a release over that
  heading (KI-23 guard).

## 10. Coordination with the RG-55 continuation, and hands off

- vbpub lanes install assay **from the judged worktree's own source**
  (`run-gate-project/run_gate.py`, `assay_source_setup`), so merging to `main`
  never disturbs a running campaign; a campaign picks up new assay only when its
  tree is reconciled onto a newer `main`. Tell the RG-55 controller when the
  release ships (version + what changed for the mutation monitor: P3).
- **Do not touch:** the `rg55-*` worktrees and branches; `assay-b099-p35-repair`,
  `assay-next-wave` and `review/assay-p35-execution-interruption-boundary`
  (B103's only copy of the carve); the four clean, merged
  `assay-b088-resume-identity`/`assay-b092-b098`/`assay-b097`/`assay-liveness`
  worktrees (RG-55's close-out list). The agent-owned
  `.claude/worktrees/agent-*` is the harness's.
- dstdns is touched only through the inbox notify and read-only measurement.

## 11. Definition of done

- P0: `CHANGES.md` folded; backlog list and status lines correct (including
  B079); B105 filed; the cmru findings filed or recorded as not reproduced.
- P1–P3, P4 (if started), and P5: carved, implemented, adversarially reviewed
  (findings fixed or explicitly ruled), the package branch gated PASS on a
  tree that already contains current `main` (merge `main` into the branch
  first if it moved), merged `--no-ff`, pushed — the release's own
  `tester-unified` run is the gate for the final tip; docs in the same package;
  decisions A-456+ and backlog B080/B081/B094/B025/B095/B076 (+B100, B106)
  carry DONE/RULED with
  commit/version evidence; B089 cross-referenced.
- Released, deployed into the devcontainer, dstdns notified, RG-55 told.
- Real-consumer confirmation for B080: the minimized real-shaped fixture passes
  end to end AND the measured dstdns artifact (read-only, one-off) parses to
  judged branch numbers for all six former specimen lines — recorded in the log.
- All wave worktrees and branches removed; controller log closed with a final
  summary; the suggested `assay analyze report` wording for AGENTS.md (if P4
  shipped) left for the operator.

## 12. Stop and ask the operator when

- any package other than P5 would need a verdict-schema bump; P5 may use v13
  for B106. Do not make a P5 lane-schema or reason-code change until the
  requested GPT-6-Luna xhigh recommendation and review is complete and
  recorded;
- the B080 fix would change B054's ruling, or would change a previously-PASS
  verdict's numbers;
- B094 can only be delivered by extending `verify.py`'s accepted reasons
  (D3 (ii)), or P4 would change an existing `analyze` subcommand's behavior;
- you think assay should honor `safe.directory` (D4) or pick B076 (b)/(c) (D2);
- any oracle cannot be met without weakening a refusal that exists today;
- a review round 3 still has a BLOCKING finding;
- the release would collide with a running RG-55 campaign and the RG-55
  controller has not agreed a window.
