# Assay v3 review — post-P33 (schema v5), full codebase

**Review date:** 2026-08-11
**Reviewed revision:** `e41ea99f71d0188a84fe26c9deb827247ec73753` (`main`, clean)
**Reviewer:** Claude Fable 5, xhigh — a genuinely independent model family from
this wave's carver/implementer/reviewer chain (all Opus/Sonnet), requested
specifically as a full-codebase check after P33's six-round carve-review saga,
hinted to watch for future Go adapter concerns and schema validity
**Scope:** open-ended review of the codebase as it stands post-P33-merge, not
scoped to any one package's oracles
**Disposition:** no blocking defect found; several real, actionable findings —
see below

> **Controller note (2026-08-11):** committed as a standalone project record
> per operator instruction, alongside the P33-per-package carve-review reports
> already under this directory. Relayed in full to C-sol-1 (the carver) for
> disposition; see `nyxloom-trove/decisions.md` and `STATE.md` for what was
> acted on. This file is the reviewer's report as returned — not edited by the
> controller.

---

# Assay code review — main @ `e41ea99f` (post-P33, schema v5)

Confirmed HEAD `e41ea99f71d0188a84fe26c9deb827247ec73753`. I read the schema, `verdict.py`, `verify.py`, `config.py`, `errors.py`, `vocabulary.py`, `registry.py`, both adapters, the parsers, the trove decision record (A-172, A-217–A-233 in full), and ran the full test suite in the devcontainer: **2377 passed, 10 skipped, 1 failed** (the failure is environment-bound, explained below). One correction to the prompt's framing: a Go adapter and `go_cover` parser *do* exist (`adapters/go.py`, 544 lines) — but they're production-unreachable, since `cli._built_in_registry` registers Python only, so any Go lane above R0 refuses at load with `BAD_LANE_CONFIG`, exactly as A-225 records.

---

## What's solid

The architecture is genuinely strong and matches its own design guide. Specifics I verified rather than trusted:

- **Three-layer artifact defense actually holds for v5.** Every P33 invariant I traced is implemented in *both* the model and the raw verifier, independently worded: operator-prefix = resolved language (`verdict.py:2341`, `verify.py:450`), equivalence pairing (`verdict.py:2391`, `verify.py:537`), kill-attribution derivation and per-kill signals (`JudgmentR2.__post_init__` at `verdict.py:1382–1406` plus `verdict.py:2412`, `verify.py:562`), base-iff-r1/r2 (`verify.py:511`, schema branch at line 1305), helpers-require-a-judged-claim (`verdict.py:2024`, `verify.py:612`), and `ALL_MUTANTS_EQUIVALENT` re-derived through the shared `judge_mutation` (`mutation.py:972`) rather than a second table. The forged-document holes I probed for (payload-free `ALL_MUTANTS_EQUIVALENT` on the MUTATION_UNSUPPORTED branch, duplicate-rigor claims, kill_signal on a survived entry) are all caught by at least one of model/verify.
- **Closed vocabularies with one leaf source of truth.** `vocabulary.py` matches the schema's `oneOf` branches exactly (order-normative, pinned by `test_verdict_schema_is_packaged.py`); `errors.py`'s outcome/reason partition is closed, non-overlapping, and enforced at `AssayError` construction. The error grammar is complete — the old A-O14 gap is closed by `OUTPUT_WRITE_FAILED`.
- **The reserved-field discipline is real.** `config.py:998–1013` refuses `kill_signal_artifact`/`equivalence_artifact` by name with a typed `LaneConfigError` naming P34, and refuses foreign-language operators with a message that distinguishes "wrong language" from "unknown operator" (`config.py:1070–1093`).
- **The gate migration kept its promises.** `tools/tester-unified-gate.sh` deselects exactly the four template-coupled P26 tests plus the marker test (A-226 as amended by A-229), keeps the other nineteen (including the A-212 process-group-kill security oracles), and runs P33's v5 sibling suite.
- **The self-lane (`assay.toml`) is honest** — R0-only permanently, with the wheel-shadowing traps (`--override-ini=pythonpath=`, dropped PYTHONPATH) documented at the point of use.

## Real risks, ranked

### 1. The committed Go coverage fixtures don't match what real Go tooling emits (HIGH for the Go track)

`tests/fixtures/go/hello/hello.out` contains:

```
hello/hello.go:29.34,30.42 1 1
hello/hello.go:36.37,37.44 1 0
```

Those blocks end at the *return statement's end column*. Real `cmd/cover` function-body blocks end at `Rbrace+1` — the closing-brace line, column 2. The project's own pinned-toolchain probe proves this: `carve-assets/P27/witness/coverage-commit1.out` records `calc.go:4.24,6.2 1 1` for a one-statement function on lines 4–6, and A-218's reasoning states outright that "`Rbrace + 1` is always ≥ 2." A real regeneration per `hello.go`'s own header instructions would produce `29.x,31.2` and `36.x,38.2`, putting closing-brace lines 31 and 38 into the executed/missing sets and breaking `test_adapters_go_union_fidelity.py`'s expected mapping (executed `{29,30}`, missing `{36,37}`). The same class is in `tests/fixtures/canary/go/greet/greet_control.out` (`19.2,19.42` — starts at the body line, real profiles start at the signature's `{`).

So the fixture the O1 union-fidelity claim rests on is hand-shaped to the "coverage lines = statement lines" assumption that the A-O19 probe disproved, and its docstring claim that "braces … are untracked by any block" is false for real profiles. It went unnoticed because the devcontainer has no Go toolchain to regenerate with (A-042). Nothing wrong ships today (Go is unreachable), but P28's qualification and P27's re-carve will collide with this, and worse, anyone auditing "Go is proven by committed fixtures" (A-009/A-042) is looking at fixtures that a real toolchain contradicts.

### 2. The schema under-enforces locally-expressible invariants its own doctrine claims to own (MEDIUM-HIGH)

The schema's top-level description (A-182) promises: "this document owns every LOCALLY expressible rule — enums, ranges, requiredness, string grammar, and reason/payload conditionals within a single object." Three families violate that, and it matters because A-029 makes the shipped schema the *only* contract external consumers get — they never run `verdict.py` or `verify.py`:

- **A hollow PASS validates.** `{rigor:"R1", source:"computed", status:"PASS", verified_by_assay:true}` with no `coverage` payload passes the schema. The model refuses it (`verdict.py:1689`, whose own docstring calls a payload-free PASS "the strongest form of exactly the lie P16 exists to catch"), but a jsonschema-only consumer accepts it. Same for R2 PASS without `mutation`, R3 PASS/FAIL/INCONCLUSIVE without `canary`, and FAIL/`MUTANTS_SURVIVED` payload-free. All are expressible as claim-local if/then branches — the schema already has branches of exactly this shape for `NO_MUTANTS`, `MUTANT_LIMIT_EXCEEDED` and `ALL_MUTANTS_EQUIVALENT`, which is what makes the asymmetry look accreted rather than designed.
- **`judgment_r2`'s own description promises an unenforced rule.** `kill_signal_artifact`'s description (schema line 1211) says "Required when kill_attribution is 'declared'; forbidden when it is 'unattributed'" — a single-object conditional with no if/then behind it. `kill_attribution:"declared"` with no artifact validates.
- **One factually wrong description.** The `ALL_MUTANTS_EQUIVALENT` branch (line 1587) claims "Whether killed + survived == 0 with a non-empty equivalent bucket is cross-object and lives in the model and raw verifier." It is *not* cross-object — `killed`/`survived`/`equivalent` all sit inside the claim's own `mutation` property, and the sibling `MUTANT_LIMIT_EXCEEDED` branch (line 1539) constrains exactly those buckets locally. This is the one instance I found of the stale/inaccurate-description class the last rounds hunted.

None of these is a producer bug — model and verify catch every case. But six review rounds closed exactly this class one branch at a time (A-228 exists because `ALL_MUTANTS_EQUIVALENT` lacked what its siblings had), and the remainder deserves one deliberate ruling: either add the branches, or amend the A-182 doctrine text to say the schema owns refusal-of-impossible-payloads but not requiredness-of-evidence.

### 3. STATE.md is stale at the project's own resume point (MEDIUM)

`nyxloom-trove/STATE.md`'s banner still says P33 "awaits a SIXTH mandatory carve review," but P33 is merged at HEAD (`e41ea99f`); the file's last commit (`6a7f9764`) predates the merge, and the P33 post-implementation repair (`62305df3`) and merge aren't recorded. For a project whose documented pickup protocol is "read STATE.md first — it is the resume point and is kept current," this is the staleness most likely to cost a future session real money. `decisions.md` *is* current (A-219–A-233 recorded).

### 4. Stale key-path/version text in source docstrings (LOW)

- `verify.py:33` — module docstring says "Checks the packaged schema v4"; the shipped schema is v5.
- `runner.py:554` — "This is how `run_lane` builds `judgment.r1.base`"; v5 moved that value to `judgment.resolved.base`.

Both are the exact class a past round already found once; neither misleads code, both mislead readers.

### 5. One permanently-cockpit-red test (LOW)

`test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape` asserts `assay_version == "0.0.0"` on the A-069/A-124 premise that setuptools_scm never loads — true in the gate image, false in this devcontainer (setuptools-scm 10.2.1 is installed, wheels version as 0.1.0). Red here, green in the gate. Doctrine says cockpit greens aren't ship signals, but a test that is *always* red in the cockpit trains people to ignore red; a `skipif setuptools_scm importable` guard would preserve the gate oracle without the noise. The 10 skips are legitimately environment-gated (shellcheck, `/opt/tester-venv`, `/proc`, root).

## Specifically relevant to future Go work (P27 re-carve, P28, P29)

Beyond risk #1 above:

- **The statement-position oracle has no seam to plug into.** A-217 rules that Go statement positions come from a source-side oracle, and the artifact side is reserved (`helpers` with role `statement-positions`, `MISSING_EXTERNAL_TOOL`). But the protocol side has no place for it: `LanguageAdapter.statement_spans` is only consulted for lines that appear in *no* coverage bucket (`base.py:185–204`, gated on `requires_span_attribution`), and `go_cover.py:68`'s `range(start, end+1)` expansion puts every block line into executed or missing — Go can never produce an unattributed line, so the oracle would never be called through the existing seam. Correcting the over-approximation requires either the parser stops expanding (emit block extents; `FileCoverage`/`CoverageProfile` in `coverage_parsers/model.py` currently has no representation for "these line sets are over-approximations to intersect with statement positions") or the correction happens at the adapter/evaluate boundary. This is the concrete shape of the cost A-217(e) accepted; the re-carve should treat it as a design item, not an implementation detail.
- **Two module docstrings still teach the disproven premise as settled.** `adapters/go.py:59–74` ("requires_span_attribution = False … settled, not assumed … structurally impossible") and `go_cover.py:15–22` ("every line in that range is executable") both state the exact assumption the A-O19 probe disproved (17 lines marked executable where the source has 7 statements), with no pointer to A-O19/A-217 in either file. A-217(b) gave `go_cover.py` a scope status in the re-carve; the file-local prose should get a banner too, or the next implementer who reads only the file re-learns this the expensive way. Same family: `_inject_uncovered_line`'s description hardcodes "(2 uncovered lines)" (`go.py:470`) — statement-truth 2, real-profile line-truth 4 under the current expansion.
- **The `go:*` vocabulary is consistent everywhere I checked.** `vocabulary.py:85–89`, the schema's second `oneOf` branch, and A-221 agree exactly (three operators, deliberately no `falsy-swap`); `GoAdapter.generate_mutation_sites` is unconditionally `"UNSUPPORTED"`; the registry refuses Go R2 at load, matching A-225's *corrected* description of the shipped behavior. A-221's implementation trap for P29 (`true`/`false` are shadowable predeclared identifiers, not keywords) is recorded. No drift found.

## Specifically relevant to schema validity

Items 2 and 4 under risks, plus observations that look deliberate but are worth stating:

- **`judgment.resolved.language` is an open string** while the operator vocabulary is closed per language. A foreign r1-only document claiming `language: "rust"` validates everywhere (schema, model, verify). Presumably deliberate — the language vocabulary is registry-owned — but it means the "closed per language" property only bites on r2 documents, through the prefix-equality rule.
- **`commit` and `resolved.base` are `minLength: 1` strings**; "the full resolved comparison commit, never a symbolic ref" is prose in all three layers — a foreign document with `base: "HEAD"` validates. A hex pattern is locally expressible (`replacement_sha256` gets one); if the looseness is deliberate (SHA-1 vs SHA-256 repos), the description should say so.
- **Go/SQL schema surface is intentional reservation, not scope creep**, and it's *documented as* reservation: `go:*`/`sql:*` operators are declarable-but-unproducible (A-221/A-225 name this), `kill_attribution: "declared"` is grammar exercised only by documents until P34 (A-230d), and the `helpers` roles map one-to-one onto ruled helper jobs (A-227). I found no reserved surface without a named owner.
- The cross-object checks the schema explicitly disclaims (rollup agreement, argv arithmetic, interval ordering, candidate arithmetic, canary/judgment equality) are all present in both `verify.py` and the model — I found none claimed-but-single-witnessed.

## P34 (SQL adapter) readiness

Mostly good: reserved fields refuse by name pointing at P34; `registry.py` supports R2-only registration (`RegistryEntry.rigor`); the byte-span site identity already fits SQL (A-215/B001 confirmed); equivalence and kill-attribution grammar are in place and doubly enforced; the per-mutant-timeout question has a named v6 trigger. Two things will bite if not settled in the carve:

1. **The flat 7-method protocol.** A SQL adapter must implement `has_executable_code`, `normalize_coverage_key`, `statement_spans`, `inject_import_break`, `inject_uncovered_line` — all coverage/canary-shaped and meaningless for an R2-only language. B001's item 3 ("does the flat `LanguageAdapter` remain honest for an R2-without-R1 language, or does it need capability-specific factoring") is still unanswered; P33 correctly didn't touch it, so it's the first design question P34's carve must rule.
2. **A helper that ran and failed has nowhere to live in the artifact.** The helpers correspondence rule (`verdict.py:2024`, `verify.py:612`) binds a `mutation-sites` entry to an R2 claim *with a mutation payload* — but `MUTATION_DISCOVERY_FAILED` is payload-free by A-183. So when the SQL parser helper is invoked and discovery fails, recording the helper's identity makes the artifact invalid; the producer must omit it, losing exactly the provenance you'd want when debugging a failed helper. That's a direct tension between A-183's payload-free terminals and V5-5's reproducibility goal, and P34 will hit it on its first unparseable DDL file.

## Everything else worth a human's attention

- `build/` and `src/assay.egg-info/` are correctly gitignored (A-057 held).
- `decisions.md` is 509 lines / ~86k tokens and is read at every orientation; the project already measured orientation at ~142k tokens per package. An index or per-era split would pay for itself within two packages.
- The one failing devcontainer test and the STATE.md banner are the two things I'd fix *before* dispatching P34, since both misinform the next session's first ten minutes.

Unrelated footnote: this session's claude.ai and Miro MCP connectors need re-authorization (via claude.ai connector settings / `claude mcp` in an interactive session) before their tools can be used — nothing in this review needed them.

I also updated the `assay-project` memory to reflect the post-P33 state (P33 merged, A-O19 ruled, P34 next, STATE.md banner stale) so the next session doesn't resume from the stale banner. Per the review's read-only mandate I changed nothing in the repository itself.

---

# Addendum 1 — discussion round (2026-08-11): open decisions + post-Go roadmap

> **Controller note:** resumed the same Fable session after the initial review
> above, once C-sol-1 had acted on it (decisions A-234–A-238; see
> `nyxloom-trove/decisions.md`). Given the carver's actual response first, then
> asked for real pros/cons and a recommendation on the decisions the carver
> left open, plus forward-looking thinking on post-Go consumer value. Verified
> against HEAD `35a6e4f34eea41692ef2f3899a89e003e2bb1339`.

Verified: HEAD `35a6e4f3`, A-234–A-238 read in full from `decisions.md`, the A-236(b) pairing branch is present in the shipped schema (lines 1219–1250), the doctrine narrowing is banner-recorded in `SCHEMA-V5-DESIGN.md:3`, and `handoffs/README.md` gates both P27's re-carve (A-234/A-235) and P34 (A-238) on the open rulings. I also read the conformance tests A-237 cites, `mutation.py`'s target resolution, and the coverage model — several of these change my answers from what I'd have said from memory.

---

# Part 1 — the four open decisions

## 1. `decisions.md` size

**The data first.** 518 lines, 259 decision rows, 45 sessions over six days. The growth is not linear in either dimension:

- **Rate:** P02–P14 added ~5–8 rows per package. P20–P26 added ~5–10 each. P33 alone added 15 (A-219–A-233, sessions 38–44), and the review response added 5 more — 20 rows in a single day.
- **Density:** the real cost is not row count but row *length*. Session 2's rows are one to three sentences. A-219, A-223, A-226 and A-229 are 300–500-word essays each. The file's own header still says: *"One line per decision, with the reason. Reasoning in depth lives in `docs/DESIGN-GUIDE.md`; this file is the record."* That contract has been abandoned in practice since roughly session 38 — the recent rows are mini-reports, and the project already has a `reports/` directory that is the designated home for exactly that prose.

So the ~86k tokens is mostly the last two weeks of entries, and at the current rate (P34, P27 re-carve, P28, P29–P32 remaining, each now running multi-round adversarial review), the file plausibly doubles before the wave ends. Paid at every orientation, that is on the order of 86k × 8 remaining orientations ≈ 700k tokens if nothing changes — against a one-time migration cost of maybe one focused session.

**The real tradeoff.** The miss-a-decision risk is not hypothetical here — this project has *twice* recorded exactly that failure class: A-072 (a ruling delivered only in an agent message resurfaced as if never diagnosed) and A-231 (a reviewer without decision history concluded the package contradicted itself). And agents are instructed to *grep by id* — which works across files only if the reader knows the other files exist. Any split that removes ids from the file agents grep first recreates the A-072 shape structurally.

But that risk is fully mitigable with one design constraint: **the active file keeps a one-line stub per archived decision** (id, ≤15-word summary, pointer to the archive file). Then an id-grep always hits in the active file; the stub tells the reader whether the full reasoning is worth fetching. 259 stubs ≈ 259 lines ≈ 10–15k tokens instead of 86k. The maintenance cost of cross-references is near zero under this shape because ids are stable and the archive is append-frozen — this is the same supersession-by-banner pattern the project already uses (`FROZEN-WAVE-CONTROLLER-PROMPT.md`). A secondary benefit nobody has named: the session-3 note records that *every edit to decisions.md invalidates the orientation snapshot* — a split means new-decision churn only invalidates the small active file.

Era-splitting *without* stubs (P00–P14 / P15–P19 / …) I'd argue against: it forces the reader to know which era a cited id belongs to, and the two renumbers (A-153/A-167) already made id-to-era mapping non-obvious.

**Recommendation** — two measures, and the first matters more than the second:

1. **Re-enforce the file's own one-line contract for new entries.** A decision row becomes: the ruling, one reason sentence, a pointer to the report/design-doc carrying the full argument. This is a zero-migration-risk process change that cuts the *growth rate* immediately, and it is not a new rule — it is the file's own header, currently violated.
2. **One mechanical archive split of the existing long-form prose**, with per-id stubs retained in the active file, done as its own reviewed chore (not bundled into a package), with a sweep-style oracle in the project's own style: a test that greps every id A-001–A-238 and requires a hit in the active file. Payback is 2–3 packages.

The carver was right not to bundle this into a review-response commit; it is genuinely operator-sanctioned work because it touches the primary authority document. But I'd sanction it — the cost compounds, the mitigation for the miss-risk is cheap and testable, and measure (1) alone doesn't recover the 86k already sunk into every future orientation.

Where I'm genuinely unsure: whether measure (1) survives contact with carve sessions under pressure — the essay-length rows exist because carve rounds needed to record evidence *somewhere* and decisions.md was the one file guaranteed to be read. If reports aren't reliably read at orientation, prose will creep back. A one-line lint (row length cap in the active file, warning not error) would hold the line mechanically.

## 2. A-235 — the Go statement-position seam

I have a real preference here, and it rests on an argument neither candidate's framing in A-235 states: **candidate (ii) is not just less clean — it is information-theoretically insufficient for one of the cases the P27 probe proved real.**

`go_cover.py:64–73` merges blocks into line sets with executed-wins-on-overlap *during parsing*. A-O19's own correction note records the consequence: block extent is a strict over-approximation for the executable and executed sets, *"though not for the missing set, which executed-wins-on-overlap can launder."* Concretely: the probe proved adjacent blocks share a boundary position (A-218). If a statement sits on a shared boundary line — the column-1 token ending an executed block and beginning a missed one — the parser's merge marks that line executed before any downstream code runs. A correction applied at the adapter/evaluate boundary operates on the already-merged `FileCoverage` line sets; intersecting `missing ∩ statement_lines` at that point cannot recover a statement the merge already collapsed to executed. Deciding which *block* a boundary statement belongs to requires block extents *with columns*, pre-merge, compared against statement *positions* (line **and column** — which A-217's `cmd/cover`-adapting oracle provides, and which the current `StatementSpan` type cannot even carry: it is line-only, `base.py:72–73`).

So the correction must see pre-merge block data. Candidate (ii) as stated ("without changing the shared model") can only get that by having the adapter re-parse the profile itself — duplicating the parser and breaking the format/language axis separation A-006 exists for.

That said, candidate (i) should be taken in a specific shape, because the naive reading ("evaluator intersects") risks the leak the question worries about:

- **The parser emits blocks** — an explicit block-extent representation in the model (an optional `blocks` field or a sibling `BlockCoverage` type; line-granular formats never populate it). This is honest about what the format actually says, which is the whole A-O19 lesson. Yes, it touches DESIGN-GUIDE §11's frozen literal `FileCoverage(executed, missing, excluded)` shape — but that shape has already been extended once by a derived property (`model.py:93–103` says so itself), and the P27 re-carve is the package that owns this change under exactly the A-084 rule (extensions added by the package that first proves the need).
- **The adapter supplies statement positions** via a *new* deliberate protocol extension, not by overloading `statement_spans` — whose contract ("called ONLY for unattributed lines," frozen by A-097/A-101) is the wrong gating direction (it rescues gaps; Go needs to demote fabrications) and whose type lacks columns.
- **The intersection lives in the core as a pure function** — this is what keeps Go out of the language-agnostic evaluator. "Given block extents plus statement positions, compute statement-granular line sets" is set arithmetic over two language-free inputs, the same division of labor as P07's rule 3b (adapter supplies spans, pure core function resolves). It is also directly testable from the committed P27 witness assets with no toolchain, preserving A-042.

On the third-language question: I checked, and the reusability payoff is probably a class of one. Istanbul's JSON (the eventual TS adapter's likely format, A-O03) carries a `statementMap` with per-statement start positions — it is *statement-precise* already, no over-approximation to correct. SQL/DDL has no coverage tool at all — v5 refuses SQL R1/R3 by construction, so there is no analogous gap to reuse this for. So do (i) **minimally, for honesty about Go's format** — not as speculative general infrastructure. If the block representation ends up Go-only forever, that's fine; the alternative was a shared model that lies.

Also worth the re-carve's attention either way: P28's qualification compares assay's tuple against covergate and the hand manifest *at statement granularity* (A-217's stated intent). Candidate (i) keeps the two facts — "what the profile said" (blocks) and "what the statements are" (oracle) — separately visible to that harness; (ii) buries the correction inside adapter code where the qualification can only see its output.

## 3. A-238(i) — the flat protocol for an R2-only language

The most load-bearing fact here is one I had to read the code to get: **the flat protocol is less meaningless for SQL than A-238's framing suggests.** `resolve_mutation_targets` (`mutation.py:398–402`) drives R2 target selection through `adapter.excluded_dir_names`, `adapter.source_globs`, and `adapter.is_test_path`. A SQL adapter genuinely needs all three (`*.sql` globs; excluding, say, a fixtures directory; distinguishing test DDL from tracked schema DDL) plus `name`, `external_tools` (the SQL parser helper — A-013's mechanism is exactly right for it), `requires_span_attribution = False`, and `generate_mutation_sites`. What's actually dead for SQL is five methods: `has_executable_code`, `normalize_coverage_key`, `statement_spans`, `inject_import_break`, `inject_uncovered_line` — all coverage/canary-shaped.

Now what the registry already buys: `RegistryEntry.rigor` (`registry.py:76–91`) plus `get_adapter(language, rigor)` means a SQL entry registered `{"R2"}` makes the five dead methods *provably unreachable through the CLI* — `cli._resolve_declared_adapters` refuses a SQL R1 or R3 lane at load, before any adapter method is called. So the honesty problem with keeping the flat protocol is not "SQL might run a meaningless method"; it is only "five stubs exist whose bodies are dead code." That reframes the choice: capability-factored sub-protocols would buy *type-level* honesty at the cost of touching the A-097-frozen `base.py`, both existing adapters, the registry's typing, and DESIGN-GUIDE §11's "flat seven-capability list" — a wide blast radius, zero behavior change, for a capability class that currently has exactly one member. Every plausible future language except SQL has coverage (TS does, Go does); a second R2-only language may never exist. A-084's own doctrine — extensions are added by the package that first *proves* the need, never spec'd ahead of it — argues squarely for deferring the factoring until that second member appears.

**Recommendation: keep the flat protocol, gate reachability through `RegistryEntry.rigor` as the design already does, and make the five dead stubs raise loudly rather than return plausible values.** The project has a named precedent for exactly this shape: `_BASELINE_NEVER_READ` (`verify.py:1053–1058`) — "deliberately NOT a plausible-looking baseline: if that proof ever stops holding, an `AttributeError` here is a loud, immediate failure rather than a silently wrong re-derivation." A SQL `has_executable_code` has no correct return value — `True` (the Go fail-closed convention) would silently manufacture a false-FAIL path if a future wiring mistake ever made it reachable; raising converts that mistake into a crash at the mistake's site. Note this is deliberately *different* from `GoAdapter.statement_spans` returning `None` — that return is *correct* under Go's declared `requires_span_attribution = False`, not a placeholder; the distinction (correct-by-contract no-op vs. no-correct-value-exists) is worth a sentence in the P34 carve so the two aren't conflated later. A raising method still satisfies the `Protocol` structurally, so nothing in typing or the registry changes.

Where I'm unsure: whether the P34 carve should also record the *trigger* for revisiting ("a second R2-only language, or an R1-capable language whose coverage methods want a different shape") — I lean yes, one line, so the deferral is a named decision with a named reopen condition rather than a default.

## 4. A-238(ii) — discovery-failure helper provenance

Two facts decide this one for me:

**First, candidate (a) requires no schema change at all — the helpers-correspondence rule was never in the schema.** It is cross-object (top-level `helpers` vs. the `claims` array), so it lives only in `Verdict._check_helpers` (`verdict.py:2024`) and `verify._check_helpers_have_a_judged_claim` (`verify.py:612`). Widening it is a model + raw-verifier edit with zero v6 exposure. Candidate (b) — a terminal shape that carries provenance without a mutation payload — means new schema surface for a failure state: either a new claim field or a new payload variant, inside `additionalProperties: false` objects. That is (1) a schema change to a contract that just cost six review rounds to freeze, and (2) precisely the "reserved-but-unusable space invites a plausible-looking implementation" hazard that A-220 and P33 refused repeatedly. It also erodes A-183's discipline: the payload-free-ness of `MUTATION_DISCOVERY_FAILED` is what keeps "no analysis happened" distinguishable from "an analysis observed nothing" — start attaching evidence-shaped structure to failure terminals and that boundary blurs.

**Second, candidate (a) has a direct in-codebase precedent.** The identical tension was already resolved once, one object over: `judgment.r2` is recorded not only when R2 rendered a payload but also for the `MUTATION_UNSUPPORTED` terminal, because "the policy was resolved and applied before discovery returned UNSUPPORTED, so dropping it would hide the cap a consumer needs to interpret the refusal" (`verdict.py:2256–2268`, A-183). The helpers case is the same sentence with "policy" replaced by "helper": *the helper was invoked before discovery failed, so dropping its identity hides exactly what a consumer needs to interpret the failure.* Widening the correspondence to "an R2 claim carrying a mutation payload OR the `MUTATION_DISCOVERY_FAILED` terminal" is the established move, not a new kind of exception.

**Recommendation: candidate (a), with three scope guards written into the ruling:**

- The widening admits **only `MUTATION_DISCOVERY_FAILED`** — the terminal that means "the engine ran and failed." `MUTATION_UNSUPPORTED` (no engine; nothing was invoked) and `MISSING_EXTERNAL_TOOL` (the helper binary is absent; it *couldn't* have been invoked) must **not** admit a helpers entry — a helper record beside either would be a contradiction, and saying so explicitly is what keeps the widening from being read as "failure terminals may carry helpers generally."
- Phrase it generically — "a helpers entry requires either a claim carrying the payload that role produces, or a claim whose terminal names that helper's own failure" — so P27's re-carve can reuse the ruling for the `statement-positions` role without rearguing it (the Go oracle will have its own failure terminal to name; that's P27's to pick, but the correspondence shape should be decided once here).
- Both layers get the widening (model and raw verifier, per A-182's independence), and the negative is tested in the P34 carve's style: a `mutation-sites` helper beside `MUTATION_UNSUPPORTED` must still be refused.

The residual case I'd flag as genuinely open rather than pretend to settle: a helper that ran *successfully* on a run that then failed for an unrelated reason (say the deadline). Under (a) that helper entry is still refusable, and probably should be — but P34's carve should decide it looking at real SQL runs, not have it decided here blind. A-238's own reasoning for not ruling now applies to this sub-case even after (a) is adopted for the main one.

## Addendum — A-237's evidence, read directly

This isn't one of the four questions, but the operator may weigh A-237 partly on its evidentiary claim, so it should be said plainly: **the conclusion is defensible, but the cited evidence doesn't support it the way the entry states.** I read the "family of four" in `tests/test_verdict_conformance.py` (lines 485, 526–536, 539–556, 560–569). All four defend *genuinely* inexpressible rules — argv cross-field arithmetic, claims-cover-declared-rigor, evidence-covers-declarations. None of them is *about* the claim-local payload rule. The test that broke (`test_verify_rejects_claims_for_an_undeclared_rigor_level`, line 539) uses a payload-free R1 PASS claim as its fixture *vehicle* for a different oracle — its assertion text explains its schema-validity expectation via "each claim is independently schema-valid," which is a premise that happens to lean on the gap, not a test defending the gap. Repairing the vehicle (give the extra claim a coverage payload) is a one-line fixture edit that leaves the test's actual oracle intact.

So "a prior package authored this gap on purpose" is not what those tests show; what they show is that prior fixtures *assumed* the gap. The narrowed doctrine (schema owns refusal-of-impossible, not requiredness-of-evidence) may still be the right boundary — it is coherent, and every case is caught in model + verify. But the door should be recorded as open, not closed by suite-refusal: A-236(b) itself just established the precedent that v5 can be tightened in place where no truthful producer emits the newly-refused shape, and the hollow-PASS branches qualify under exactly that test. If a jsonschema-only consumer (the A-029 population) ever materializes, that's the trigger to revisit — and the revisit should sequence the fixture-vehicle repairs and the schema change as separately reviewed steps, which addresses the process concern A-237 correctly raises about editing layer-independence tests in the same commit that changes the layer.

---

# Part 2 — what comes after Go, for consumer value

The pattern I kept hitting while reading this codebase: **the write side is now over-built relative to a read side that doesn't exist.** Five schema versions, three independent enforcement layers, six adversarial rounds for v5 — and *zero consumers read a verdict artifact*. Exit codes are the only output anything in the estate consumes; `ciu test` "does not exist today" (A-014); the entire claims/evidence/judgment structure — the majority of the engineering — is write-only. Meanwhile the freshest consumer signal in the repo (B44, filed yesterday from dstdns: 21 phantom rows in a production table before anyone noticed a gate ran against the wrong checkout) is about provenance and integration, not about missing rigor tiers. The bottleneck has moved from evidence *production* to evidence *consumption* and *adoption cost*. That frames my three answers, in priority order.

## 1. The highest-value capability: fail-before/pass-after (A-O06)

Every other backlog item buys one consumer or one language. This one upgrades **every adopted lane simultaneously, in every language, with zero adapter work** — and that claim falls directly out of the architecture I read:

- It is argv-level and language-free at the core: snapshot the base commit (P22/P26 built the commit-addressed snapshot, isolation, and deadline machinery), overlay the changed *test* files from HEAD — identifiable from the diff via `is_test_path`, an adapter capability that already exists for both shipped adapters — run the *whole declared argv*, require FAIL; run at HEAD, require PASS. No test selection is ever derived, which matters because impact-based selection is explicitly the caller's domain (A-012/A-036) and any design that "runs just the new test" would violate assay's own doctrine. Running the full declared command both times stays inside it.
- It answers the question human reviewers actually ask — *"does this bugfix's test catch the bug?"* — which no tool in this estate answers today, and which R1 structurally cannot (a test can execute every changed line and assert nothing; that's what R2 and R3 exist to catch, expensively). Fail-before/pass-after gets a large fraction of R2's evidential value at R0-level cost: one extra suite run.
- The judging machinery is the canary inverted, exactly as A-O06's own entry says — `judge_canary`'s cause-sensitive comparison shape ports directly, and the closed-vocabulary discipline needs only one new mechanism name and terminal.

Notably, it also improves the value proposition for consumers that *haven't* adopted: it works for an R0-only lane's language the day the lane exists, so a project like mdt (A-O13, testable surface unknown) or srdm gets real rigor evidence before any coverage tooling is wired at all. If a package in this family has spare room, A-O07 (serial/parallel coverage parity) is its natural sibling — both are "run the declared lane twice under one controlled variation and compare" — but A-O06 is the one with review-workflow pull.

## 2. The highest-value infrastructure: close the loop on verdict artifacts

The §8 ratchet argument — adoption declares and verifies, does not remediate, because changed-line coverage *ratchets* — is inherently temporal, and nothing in the estate can currently see the ratchet. The minimal honest version is small: an `assay compare <a.json> <b.json>` (or `assay report` over a directory of verdicts) that diffs claims across commits/runs — new missing lines vs. resolved ones, mutation-score movement with the `equivalent` bucket excluded as v5 specifies, canary and attestation staleness across time. It reads the schema assay already ships, needs only stdlib (the zero-dep constraint holds), and — this is the real payoff — **it would be the first genuine A-029 consumer**, exercising the "consumers read a JSON Schema shipped as data" contract that five schema migrations have been engineered for on faith. My Part-1 addendum is a concrete example of why that matters: whether the schema needs the hollow-PASS branches depends entirely on whether jsonschema-only consumers exist; building the first one settles design questions that are currently argued in the abstract.

I want to be careful with the obvious counter, because it is this project's own doctrine: A-014 refused to name a file for an absent consumer, and a dashboard for nobody repeats that failure. But the situation is not symmetric — real lanes *already emit* these artifacts (assay's own gate, the P25 topos qualification), so a comparer leverages outputs that exist rather than naming inputs for a tool that doesn't. Scope it as a CLI verb over files, not a service; let `ciu` wrap it later if `ciu test` ever materializes.

## 3. The cheap unlock nobody has sequenced: make the covergate replacement actually land (A-O04)

This is the one where reading the record changes the picture most. The prompt frames "Go lands → assay can replace covergate." But A-O04 says the *actual* srdm blocker is orthogonal to Go capability and always was: *"Depends on whether Python enters srdm's toolchain/container. Independent of the Go adapter (A-042)."* P27–P31 can all merge and srdm still can't run assay, because assay is a Python tool and srdm's gate container has no Python.

The estate is unusually well-positioned to dissolve this, and the pieces are already in the record: assay has **zero runtime dependencies by construction** (A-005 — the whole point of the constraint), which makes it a candidate for a single-file `zipapp` needing nothing but a `python3` interpreter; and the Go gate image is already estate-owned (`vbpub/tester-unified-go`, promoted by A-043), so "Python enters srdm's container" is one measured Dockerfile change to one shared image, not an srdm toolchain-policy war. And the demand side is real, not speculative: covergate has a live known defect (it silently skipped a package's coverage in srdm's own recent history), which is precisely the four-copies-divergence failure assay exists to end.

Concretely: attach a small probe to P28 (which already runs against srdm's real repo for qualification) that measures the image-size/pull-time cost of adding `python3` + the assay wheel or zipapp to `tester-unified-go`, and turns A-O04 from an open question into a costed decision. Without this, the Go wave's headline payoff stays theoretical after all six packages merge.

## What I'd argue *against* prioritizing, and why

- **TypeScript adapter (A-O03):** its own recorded unblock condition — dstdns's `webapp-ui-react` in scope for a lane — hasn't occurred, and it carries an unresolved design question (does the TS adapter shell out to `tsc`/`node`). Building it first inverts the project's consumer-pull discipline; note also that istanbul's statement-precise format means it needs none of the Go statement-oracle machinery, so there's no sequencing synergy to capture by doing it early.
- **Tier-2 SAST/SBOM (A-O10):** deferred pending a chosen tool, and that's correct — A-034/A-078 already refused a zero-integration registry as "decorative." The tier's schema slot is stable; the first integration should be pulled by a consumer naming a tool.
- **Async Tier-3 producers (A-O08):** blocked behind claim-level `enforcement` design (A-O09) by the project's own analysis; sequencing it before A-O09 would improvise externally visible grammar, the exact move this project keeps catching itself avoiding.

**Summary recommendation for the post-Go horizon:** A-O06 as the next capability package (biggest evidence gain per unit cost, all consumers at once, machinery already paid for), the verdict comparer as the next infrastructure package (first real A-029 consumer; makes the ratchet visible; settles open schema-boundary questions with evidence instead of doctrine), and the A-O04 packaging probe attached to P28 (so the wave's stated payoff — retiring covergate — actually has a deployment path when the code is ready). All three are read-side or leverage-side; that is deliberate, because the write side is, by a wide margin, the most rigorously proven part of this codebase already.
