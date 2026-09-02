# Assay waves after 3.1.0 — the JS-consumer wave (A) and the producer wave (B)

**Written 2026-08-30 by the 3.1.0 design review**
(`reports/assay-3.1-js-adapter-design-review-2026-08-30.md`). Filed work:
B041–B048 in `4-backlog.md`; B037 resolved; ciu CIU-72/73. This file is the
dispatch plan plus the ready-to-paste implementer prompt for Wave A; Wave B's
prompt is derived from the same template once A has shipped (its §"Wave B"
below carries everything that differs).

## Status (updated 2026-08-30, post-3.2.0)

**Wave A shipped as assay-v3.2.0** — reviewed 2 rounds (fresh Opus, ACCEPT-
conditional then ACCEPT), merged `--no-ff` to main (`71ddc7d9`), released,
deployed, dstdns notified. B049 (Vitest's `coverage.clean` default orphans
assay's coverage-artifact reservation) filed with a working documented
workaround and 4 candidate fix options, none implemented — a separate,
smaller follow-up, not part of Wave B/C. Full detail: memory `assay-project`
and `nyxloom-trove/reports/assay-WAVE-A-js-consumer-REPORT.md`.

**Operator ruling, confirmed 2026-08-30: Wave B and Wave C run
BACK-TO-BACK in the order below, but Wave C's dispatch waits for Wave B's
own real RELEASE (4.0.0 published + deployed + dstdns-notified), not merely
its merge to main.** Do not start Wave C's implementer while Wave B is only
merged-but-unreleased — the whole reason for this order is that Go's carve
consumes `producer`/`helpers[]` as SHIPPED, stable fields, not as
in-flight ones on an unreleased major.

**Wave B shipped as assay-v4.0.0** (2026-08-31) — reviewed 1 round to
ACCEPT-conditional (one real blocker: a `cwd`+`link_paths` collision over
the same path silently ran the lane in the real working tree, not the
snapshot) plus a trivial fix-verification round, both resolved; merged
`--no-ff` to main (`5692ad37`), released (gate 793.2s, tagged
`assay-v4.0.0`), deployed, `CHANGES.md` cleared, dstdns notified
(`landed: B045, B043, B041(b), B046/B037`). B050/B051/B052 filed, not
built, correctly deferred. Full detail: `reports/assay-WAVE-B-producer-*`
(LOG/REPORT/CONTROLLER-LOG). **Wave C dispatch follows below.**

## Sequencing, and why

| wave | version | verdict schema | contents | why this order |
|---|---|---|---|---|
| **A — JS consumer** | 3.2.0 (minor) — **SHIPPED** | v8 unchanged | B044 `assay lanes --json` · B042 docs · B041 (a)+(c) offline-closure pattern + real-`vitest` qualification · B039/B047-4 shared expansion bound · B048 docs + fixture | everything the first consumer needs to write a correct lane, and everything ciu's v8 carve needs to consume (CIU-72), with zero schema cost — ships in days |
| **B — producer** | 4.0.0 (MAJOR, `feat!:`) — NEXT | **v9** | B045 declared producer (+ real arcs, type-only lexer, `vitest-v8` refused by name) · B046 ingested R2 (Stryker via `mutation-report-json`) · B043 `cwd` · B041 (b) `link_paths` · `judgment.r2.producer`, `cwd_declared`, `snapshot_policy.link_paths`, `judgment.r1.coverage_producer` | one bundled cut (operator ruling 2026-08-30): every field the verdict must witness lands in one bump with one migration note; the Go wave reuses the keys |
| **C — Go** | later (after B's RELEASE, not just its merge) | v9 (no further bump expected) | P27 re-carve per A-217/A-239 + B047 items 1–3, 5–6 | after B, so `producer`, `helpers[]`-in-LaneResult and the shared bound already exist as shipped, stable fields |

Out of scope for A and B: R3 registration for `javascript` (B041's last
acceptance box — only after the real-`vitest` canary pair has run), the Go
adapter beyond the shared bound, a detached `assay judge` verb (B048: not
before B004).

## Roles

- **Implementer:** ONE fresh session per wave, **Opus/Fable xhigh** (both
  waves carry design judgment — B041's contract, B045/B046's schema — not
  mechanical packages). Never a fork.
- **Reviewer:** one fresh Opus xhigh session, spawned at round 1, kept and
  RESUMED via SendMessage for every later round (memory
  `assay-carver-terra-handoff`); blind pass first, then reconcile against
  LOG/REPORT; 3-round cap, then stop and ask the operator.
- **Controller (the operator's session):** merges `--no-ff`, runs
  `cmru release --project assay` from `/workspaces/vbpub` (A-335: the real
  release is the only ship signal), deploys the wheel into
  `/home/vscode/.venv` (sha256 against the sidecar), clears `[Unreleased]`
  in CHANGES.md after the cut, drops the dstdns release-notify
  (`.assay-inbox/release.json`, memory `assay-dstdns-release-notify`) with
  the B041 lane pattern as its "action required".

---

## Implementer prompt — Wave A

```text
You are the implementer for assay WAVE A ("JS consumer wave", target release
assay-3.2.0) in /workspaces/vbpub. Fresh session: you inherit nothing, you
verify everything you claim.

READ FIRST, IN FULL, IN THIS ORDER
1. /workspaces/vbpub/AGENTS.md and /workspaces/vbpub/CLAUDE.md (estate policy,
   worktree protocol, shared-main committing, gates).
2. assay/README.md; assay/docs/DESIGN-GUIDE.md §0, §4, §5, §10, §11;
   assay/docs/CONSUMERS.md (whole file — you will edit it).
3. assay/nyxloom-trove/reports/assay-3.1-js-adapter-design-review-2026-08-30.md
   (the review that filed your work) and
   assay/nyxloom-trove/reports/assay-B036-js-adapter-REPORT.md §2, §6a.
4. assay/nyxloom-trove/4-backlog.md: B041, B042, B044, B047 (item 4 only),
   B048, and B039 — these are your contracts. Read B045/B046/B043 too, ONLY
   so you do not implement any part of them (they are Wave B).
5. assay/nyxloom-trove/decisions.md rows A-334, A-335, A-340..A-346, A-253,
   A-272 (false-refusal warning), A-007.
6. assay/nyxloom-trove/WAVE-PROMPT-2026-08-30-js-consumer-producer.md (this).

WORKTREE
  cd /workspaces/vbpub && git worktree add .worktrees/assay-wave-a-js-consumer \
      -b feature/assay-wave-a-js-consumer main
Work ONLY inside that worktree. Touch ONLY assay/** (plus nothing else —
CIU-72/73 are ciu's; the review report and this prompt are read-only).

SCOPE, IN ORDER (each step: implement → tests → docs → LOG entry → commit)
  1. B044  `assay lanes --json [--file PATH]` — the inventory exactly as the
           entry's JSON sketch, `inventory_schema: 1`, every field with ONE
           producer (the loaded Lane/JudgeConfig + the registry entry), exit 2
           and EMPTY stdout on a lane file that fails to load. Golden JSON
           tests over the existing lane fixtures (a javascript, a sql and a
           delegating lane must appear). Keys that belong to Wave B
           (`cwd`, `link_paths`, `coverage.producer`) are emitted as
           null/[] now so the shape is stable.
  2. B042  the five documentation corrections, with the exact replacement
           wording or better; grep README/CONSUMERS/DESIGN-GUIDE/the parser
           docstring for "Jest" and "interchangeable" when done. If you can
           reproduce tests/fixtures/coverage/probe-js-provider-defect under
           `c8` cheaply (node is on PATH in this devcontainer), commit the
           artifact + PROVENANCE.md entry and state the measured result
           instead of "untested".
  3. B041 (a)+(c) — the CONSUMERS section "JavaScript lanes and the
           dependency closure" (mechanism, pattern (a) with the worked
           MONOREPO lane, the image-side npm-cache recipe, the `npx` fetch
           hazard and `--no-install`, the `environment_command` caveat, the
           R3 cost, and a one-paragraph preview of (b) `link_paths` marked
           "Wave B / schema v9"), plus the qualification harness
           tests/qualification/test_javascript_real_vitest.py: skipped in the
           registered gate with a NAMED reason (tester-unified has no Node,
           DESIGN-GUIDE §10), enabled by ASSAY_NODE_QUALIFICATION=1 when
           node/npm are on PATH; it builds an npm cache from
           tests/fixtures/coverage/probe-js/package-lock.json, drives a lane
           of pattern (a) through the REAL CLI (`assay run`) in a two-commit
           git fixture, and asserts a PASS verdict and a FAIL verdict naming
           the uncovered line. Run it here (node IS on PATH in this
           devcontainer) and paste BOTH transcripts into the REPORT. This is
           the A-335 proof the 3.1.0 wave did not have; do not replace it
           with a heredoc.
  4. B039 / B047 item 4 — ONE shared classified-line ceiling in
           coverage_parsers/model.py used by go_cover AND
           coverage_istanbul_json; go_cover refuses ERROR/UNREADABLE_ARTIFACT
           past it with a paired must-succeed control over a real profile;
           the istanbul parser's own constant becomes the shared one.
  5. B048  the CONSUMERS section "Browser coverage of a UI as an R1 lane"
           (the recipe and the limit paragraph), plus a small committed
           vite-plugin-istanbul artifact produced OUTSIDE assay from
           probe-js (PROVENANCE.md entry: node/vite/plugin versions,
           lockfile), proving the keys are the original src/*.ts(x) paths,
           with one parser test over it.

NOT IN SCOPE — STOP AND WRITE A DECISION ASK IN THE REPORT INSTEAD
  - anything that adds or changes a verdict field, a schema const, a
    verify.py registration, or the frozen drift-guard
    (carve-assets/W4/verdict.schema.v8.json). Wave A ships on v8, period.
  - registering javascript at R2 or R3 in cli.py's registry.
  - a lane-level `cwd` (B043), `link_paths` (B041 b), `producer` (B045).
  - any Go adapter change beyond the shared bound.

RULES YOU ARE HELD TO
  - A-334: a test double is not evidence about an external system. Every
    claim about vitest/npm/c8/vite-plugin-istanbul behaviour is a committed
    real artifact plus its PROVENANCE.md entry, or a transcript in the
    REPORT.
  - A-335: `pytest tests/` green is not gate-green; gate-green is not
    `cmru release`-green. You run the registered gate; the controller runs
    the release.
  - DESIGN-GUIDE §5: no invented defaults, cite a source for every
    convention you document (Vitest's own include/exclude globs, npm's
    --offline/--no-install semantics — quote the docs you read).
  - decisions.md is APPEND-ONLY: record A-347+ for every design call you
    make (at least: the inventory's field set and stability rule; the
    shared bound's value and why; the qualification harness's place in the
    gate).
  - CHANGES.md `[Unreleased]`: one Added/Changed/Fixed/Documentation bullet
    per landed item, conventional-commit style, no `!` marker (minor bump).
  - Commit with `git commit --only -- <paths>`; prefixes feat(assay)/
    docs(assay)/test(assay)/fix(assay)/backlog(assay); trailer
    `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.
  - Backlog hygiene: tick acceptance boxes ONLY with file:line evidence in
    the REPORT; file genuinely new findings as B049+ (never fold new
    evidence into an unrelated entry); note existing entries when you add
    evidence to them.

GATE (run it yourself, from /workspaces/vbpub, AFTER the last commit)
  bash assay/tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-wave-a-js-consumer
Read the verdict in a SEPARATE step (exit code + the
ASSAY_REGISTERED_GATE_COMPLETE=1 marker), never as a pipe tail. Paste the
transcript head/tail and the commit it judged into the REPORT. If the gate
is red, fix and re-run; never claim green on an earlier commit.

LOG / REPORT
  assay/nyxloom-trove/reports/assay-WAVE-A-js-consumer-LOG.md — one entry per
  commit (hash, files, what and why, tests added/changed).
  assay/nyxloom-trove/reports/assay-WAVE-A-js-consumer-REPORT.md — per
  backlog item: every acceptance box with file:line evidence; the two
  qualification transcripts; the c8 measurement (or why not); the docs
  disposition table (every file touched, what changed); decisions recorded;
  "what a reviewer should push on"; and a section "what I did NOT do and
  why" (Wave B boundaries you hit).

CHECKPOINT CLAUSE
  ARM at ~120k context or ~60 tool calls (whichever first); CUT at the next
  coherent boundary (green gate > commit > LOG/REPORT write > edit-cluster
  end; never on a red gate): write a continuation brief to
  reports/assay-WAVE-A-js-consumer-BRIEF.md, author your own /compact
  retention prompt (KEEP current step + gate state + load-bearing file:line
  seams; DROP resolved sub-threads), commit, return. Repeat every ~40-55
  calls; stop when fewer than ~40 calls of work remain.

BLOCKED
  If a contract in B041/B042/B044/B047-4/B048 cannot be met as written, do
  not improvise a product ruling: implement everything that does not
  depend on it, write the exact question under "decision asks" in the
  REPORT, commit, and return.

Claim only what you ran — a fresh adversarial reviewer verifies every claim
before the controller merges, and the controller runs the real release.
```

## Reviewer prompt — Wave A (round 1; later rounds resume the same session)

```text
You are a FRESH adversarial reviewer for assay Wave A in /workspaces/vbpub,
branch feature/assay-wave-a-js-consumer (worktree
.worktrees/assay-wave-a-js-consumer). Your job is to BREAK this before it
merges. Blind phase first: read the diff `main...<tip>`, the backlog
contracts (4-backlog.md B041, B042, B044, B047 item 4/B039, B048) and the
review that filed them
(reports/assay-3.1-js-adapter-design-review-2026-08-30.md) BEFORE the
implementer's LOG/REPORT; only then reconcile against their claims.

Push on, at minimum: (1) the qualification harness — re-run it yourself
(node is on PATH here), mutate `_paint` or the fixture and confirm the FAIL
verdict actually fails for the stated line; (2) the shared bound — plant a
999999999-line go-cover block and an istanbul extent past the ceiling and
watch the refusal, plus a real profile still parsing; (3) `assay lanes
--json` — every field has one producer (grep for a second derivation), the
refusal path leaves stdout empty, the golden files cannot pass vacuously;
(4) docs — every convention cites a source, no sentence still presents the
two Vitest providers as interchangeable, "Jest" is scoped, the monorepo lane
in CONSUMERS actually runs (try it against probe-js in a scratch git repo);
(5) Wave B leakage — nothing touched verdict.py, verify.py, the schema, or
the drift-guard; (6) run the registered gate yourself on the tip.

Verdict: ACCEPT / ACCEPT-conditional / REJECT with numbered blockers,
file:line evidence and a concrete prescription per blocker; product calls
are decision asks, never improvised. Then write a "## SELF-COMPACTION
PROMPT" section so you can be resumed for the fix-verification round.
```

## Wave B — what changes in the prompt

Same skeleton; substitute:

- **Worktree/branch:** `.worktrees/assay-wave-b-producer`,
  `feature/assay-wave-b-producer`, from `main` AFTER 3.2.0 is released.
- **Scope, in order:** B045 (vocabulary, loader refusals incl. `vitest-v8`
  by name, `judgment.r1.coverage_producer`, real arcs under `istanbul`, the
  narrow type-only lexer) → B046 (`mutation_parsers/mutation_report_json.py`,
  registry, loader, the REAL Stryker fixture from probe-js with PROVENANCE,
  scope intersection, bucket map, `judgment.r2.producer`/`producer_tool`/
  `survived_uncovered`/`discarded`/`lines_without_candidates`, javascript
  registered at `{"R1","R2"}` through the ingested path only) → B043
  (`cwd`, `cwd_declared`) → B041 (b) (`link_paths` rules 1–6,
  `snapshot_policy.link_paths`, the teardown-preserves-target canary).
- **Schema:** `VERDICT_SCHEMA_VERSION = 9` (hard cut, A-138/A-170); every new
  field registered in the schema, the dataclass AND `verify.py` (the third
  place — the 2.4.0 lesson); new frozen drift-guard
  `carve-assets/W5/verdict.schema.v9.json` with the W4 asset kept as history;
  `assay verify` refuses v8 exactly as it refuses v7.
- **Commits:** the schema-cut commit carries `feat(assay)!:` so cmru's
  auto-versioning produces 4.0.0 (the 3.0.0 precedent — a `!` anywhere in the
  range is taken literally; put it on exactly one commit).
- **CHANGES.md:** a "Migration notes (v8 → v9)" block under `[Unreleased]`:
  every `coverage-istanbul-json` lane must add `producer = "istanbul"`; Python
  lanes unchanged; v8 verdicts refused by `assay verify` at v9.
- **Decisions:** the B037 rulings (backlog text) become A-rows verbatim in
  spirit; B038/B040/B037 marked RESOLVED with the row ids.
- **Reviewer emphasis:** cross-bucket invariants on both committed real
  artifacts for the arcs; the type-only lexer's fail-closed controls;
  `projectRoot` mismatch and `Pending` refusals for the Stryker report; the
  `stryker:` namespace not admitting assay-native names; teardown never
  following the `link_paths` symlink (plant the canary); `verify.py`
  re-derives `pct` for ingested R2 from the payload.
- **Release-notify to dstdns:** the `producer = "istanbul"` action, the R2
  lane shape, `cwd`, `link_paths`.

## Wave C — dispatched 2026-08-31, post-4.0.0

**Pre-dispatch currency check (controller, 2026-08-31):** `STATE.md`'s older
language ("P27 is still NOT dispatchable") describes the *pre-A-217* state
and is stale for the ruling itself, but not wrong about sequencing — A-217
(2026-08-11) ruled A-O19 and simultaneously required P27 to be **re-carved**
around the oracle rather than dispatched as originally scoped; this wave
*is* that re-carve. `3-roadmap.md` (current as of 2026-08-24) lists M6 as
next after M4 (ship, done) and M5 (SQL/P34, done) — clear to proceed.
`2-product-definition.md`'s F008 has 5 acceptance criteria; F008-A1/A2 are
already `proven`/shipped (registration, fail-closed `has_executable_code`).
**M6 delivers only the three `absent` ones (F008-A3/A4/A5)** — this is the
current, authoritative scope, replacing the older `STATE.md` P27→P32
sequence's broader "P30 real Go R2 / P31 real Go R3" language, which no
later roadmap or backlog entry re-authorizes. Go R2/R3 stay out of scope;
`generate_mutants` stays unconditionally `UNSUPPORTED` for Go.

Same skeleton as Wave A/B; substitute:

- **Worktree/branch:** `.worktrees/assay-wave-c-go`,
  `feature/assay-wave-c-go`, from `main` AFTER 4.0.0 (done).
- **Scope, in order** (each: implement → tests → docs → LOG entry →
  commit):
  1. **A-239's accepted shape, carved concretely.** `go_cover.py` emits
     block extents as an explicit representation (`blocks` field or sibling
     type on `FileCoverage`/`CoverageProfile` in `coverage_parsers/model.py`
     — YOU design the exact shape, per A-084: the package proving the need
     owns the extension). Statement positions arrive through a **NEW**
     protocol hook on `LanguageAdapter` — never by overloading
     `statement_spans` (A-097/A-101 freeze its "called only for
     unattributed lines" contract; A-239 explicitly rejected correcting at
     the adapter/evaluate boundary as information-theoretically
     insufficient — read A-239's full reasoning column before choosing a
     shape). The intersection (block extents + statement positions →
     statement-granular line sets) lives in the core as a pure,
     language-free function (P07's span precedent), **built Go-specific,
     not shared infrastructure** (A-239: no third consumer to amortize
     against — TypeScript's likely format is already statement-precise,
     SQL has no coverage tool).
  2. **The oracle itself — B047 item 1.** Ship `assay/helpers/go/stmtpos/`
     (stdlib-only Go source) inside the wheel, invoke via `go run <path>`;
     `GOFLAGS=-mod=mod GOPROXY=off` proven in your own probe (no module
     download); `helpers[].identity` records `go version …`. Adapt
     `cmd/cover`'s own instrumenter algorithm (`golang/go`
     `src/cmd/cover/cover.go`, BSD-3-Clause, `Visit`/`addCounters`/
     `statementBoundary` — A-217's own implementation note: "adapt, do not
     invent"), never `golang.org/x/tools/cover`'s `Profile.Boundaries()`
     (interpolates byte offsets between block positions, does no AST work).
     Check the oracle against **every** frozen witness in
     `carve-assets/P27/witness/` — read `carve-assets/P27/README.md` first,
     it is carver-owned, **do not edit those files**:
     - `collision-col{A,B}.go` + `coverage-collision.out` — the
       impossibility proof itself; a correct oracle must derive `{4,6}` for
       A and `{4,5}` for B from SOURCE, given the byte-identical profile.
     - `seg.go` — the discriminator that kills rules fitted only to the
       original four witnesses.
     - `lit.go` — the laundering caveat: an uncovered func-literal body
       promoted by its covered enclosing block; the over-approximation
       relation fails for the MISSING set here, not just the executed set.
     - `shapes.go` — the half-open proof (shared boundary positions).
     - `edge.go` — the end-column-1 case, and the proof it discriminates
       nothing (A-218) — do not build a rule that depends on it.
     `pinned-environment.json` names every image/toolchain input; probe via
     the tar-pipe pattern `carve-assets/P27/README.md` documents (this
     devcontainer's `/tmp` is not Docker-visible at the same path — no bind
     mount), `tester-unified-go:local`, `--network=none`.
  3. **F008-A4 — regenerate the committed Go coverage fixtures**
     (`tests/fixtures/go/hello/hello.out`,
     `tests/fixtures/canary/go/greet/greet_control.out`), sequenced AFTER
     items 1–2 land (A-234's own warning: swapping in a real profile before
     the oracle exists would just replace a wrong profile with a real one
     still read as statement truth — the identical conflation A-O19 exists
     to remove). Real bytes are already captured at
     `carve-assets/P27/witness/coverage-hello-fixture-REAL.out`; the
     correct EXPECTED assertions depend on your new oracle, not on that
     file alone.
  4. **B047 item 2 — `external_tools = ("go",)`** on the Go adapter. A-253
     already assigns the `MISSING_EXTERNAL_TOOL` preflight mechanism to
     P34, built and tested — you only add the declaration, nothing new to
     build.
  5. **B047 item 3 — `judge.coverage.producer` for `go-cover`.** Closed
     vocabulary `go-test` (`go test -coverprofile`) | `covdata` (`go tool
     covdata textfmt` over `GOCOVERDIR` binary data from `go build -cover`
     binaries — the integration-test path, an S3 lane). Uses B045's
     already-shipped producer-key mechanism; document both names and what
     each measures.
  6. **B047 item 5 — gate envelope.** Confirm `LaneResult` copies
     `helpers[]` verbatim for a Go lane (likely already true structurally
     from the Python/JS waves); this is a verification + documentation
     item unless you find a real gap, in which case fix it and say so.
  7. **F008-A5 — qualify against srdm's own Go covergate on the same
     commits** (`shared-ramdisk-depot-manager/tools/covergate`), so union
     fidelity is mechanical, not a review question. Known caveat: memory
     records covergate silently skipped one package (P14) in a past run —
     if your qualification disagrees with covergate, check whether
     covergate itself is the one with the gap before assuming assay is
     wrong; name whichever side you conclude is right, with evidence.
- **NOT IN SCOPE — STOP AND WRITE A DECISION ASK IN THE REPORT INSTEAD:**
  Go R2 (`generate_mutants` stays `UNSUPPORTED` — no roadmap/backlog entry
  authorizes building it this wave) or R3; anything for `javascript`/`sql`;
  a verdict wire-schema field. Item 1's `blocks` field lives in
  `coverage_parsers/model.py` (an internal representation `verify.py`/the
  wire schema never sees) — **expected to need NO schema bump**, same v9,
  same drift-guard (`carve-assets/W5/`); if your own design genuinely
  cannot avoid a wire field, stop and write the decision ask rather than
  cutting v10 yourself.
- **Rules, in addition to Wave A/B's (A-334/A-335/DESIGN-GUIDE §5,
  decisions.md append-only, commit/CHANGES.md/backlog hygiene):** A-042/
  A-043 (no Go toolchain in this devcontainer, ever — `tester-unified-go`
  only); A-087 does NOT transfer as precedent for a hand-guessed oracle
  (read A-217's own paragraph on why `has_executable_code`'s
  fail-closed permissiveness does not excuse a wrong statement position).
- **Commits:** no `!` expected (no wire-schema change) — plain
  `feat(assay):`/`fix(assay):`, cmru computes a MINOR bump. Do not force a
  `!` to match Wave B's pattern; that pattern was the schema bump, and this
  wave is designed not to need one.
- **CHANGES.md:** Added/Fixed bullets; no "Migration notes" section unless
  a decision ask forces a schema change after all.
- **Decisions:** append A-rows for the concrete `blocks` representation and
  the new protocol hook's exact signature (A-239 explicitly defers both to
  this re-carve); mark F008-A3/A4/A5 `proven`/status-updated in
  `2-product-definition.md` with evidence, matching F008-A1/A2's existing
  style.
- **Reviewer emphasis:** re-run the oracle against all six witness
  directories yourself, not just the ones the implementer cites; confirm
  `collision-col{A,B}` really do get resolved to different statement sets
  from source (not from the identical profile bytes); confirm `lit.go`'s
  missing-set failure is actually fixed, not just the executed-set;
  confirm `GOPROXY=off` really holds (no accidental network/module fetch)
  in the helper probe; confirm nothing touched `verdict.py`/`verify.py`/
  the schema/the drift-guard without a logged decision ask; the srdm
  covergate qualification's real PASS/FAIL agreement on real commits
  (A-208 — not vacuous agreement); run the registered gate yourself on the
  tip.
- **Release-notify to dstdns:** low urgency unless dstdns has a Go lane
  today (check `.assay-inbox` history / dstdns's own `assay.toml` before
  assuming) — if it does, the accuracy fix (statement-granular Go R1) is
  the one-line summary; otherwise a brief FYI is enough, no action-required
  framing.
