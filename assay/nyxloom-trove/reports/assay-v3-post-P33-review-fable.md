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

---

# Addendum 2 — round 3 (2026-08-11): review of C-sol-1's work, Open-section
# deep-dive, full decision-validity audit, and two operator follow-ups

> **Controller note:** resumed again after C-sol-1 formalized the three
> accepted recommendations (A-239/A-242/A-243) and independently re-examined
> its own A-237 (superseded by A-240, with a new gap A-241 found and
> deliberately left open). Asked Fable to review that work's fidelity, then
> independently re-verify the empirical claims in A-240/A-241 by driving the
> real shipped verifier, deep-dive the "Open — NOT decided" section, audit
> every decision A-001-A-244 for continued validity, and answer two follow-up
> questions (a concrete verdict-comparer example; whether assay can ship
> without a consumer-side Python dependency). Verified against HEAD
> `7fcf1020be6127c46e876839381aa1b879187105`. The controller independently
> spot-checked the report's central claim about committed R2 FAIL fixtures
> before relaying it further and found it accurate.
>
> **Headline: this round found that C-sol-1's own second empirical attempt
> at A-240 was also measurably wrong** — not the original A-237 evidence,
> which C-sol-1 had already corrected, but the *replacement* justification.
> The restored schema branches C-sol-1 tested were broader than the actual
> recommendation (they included an R2-FAIL-requires-payload branch that was
> never part of what was accepted), and that overbroad branch — not the
> correctly-scoped one — is what broke the existing test. Under the
> correctly-scoped branch set, Fable swept all 49 committed v5 verdict
> documents and found zero breaks. Relayed back to C-sol-1 for independent
> re-verification, not accepted at face value.

All verification complete. HEAD confirmed at `7fcf1020`; everything below is grounded in the current tree, and every empirical claim was produced by driving the real shipped verifier, not reading it.

**The headline first, because it's the one that changes a recorded decision: A-241 is confirmed exactly as written, but A-240's replacement evidence is measurably wrong, and the revert it justifies doesn't follow from what I measured.** Details in Part 2.

---

# Part 1 — Fidelity of A-239 / A-242 / A-243

## A-239 (Go seam) — faithful, and the "information-theoretic" framing holds as scoped

The landed shape is my recommendation transcribed: parser emits block extents as an explicit representation, statement positions arrive through a **new** protocol hook (not `statement_spans`, whose "only for unattributed lines" gating and line-only, column-less type I flagged), the intersection is a pure language-free core function on the P07 rule-3b pattern, and it's built Go-specific because Istanbul is statement-precise and SQL has no coverage tool. All four elements match, including the reasoning.

On the "strengthening": the prompt's framing has it backwards — "information-theoretically insufficient" was *my* round-3 wording; A-239 transcribed it, not strengthened it. Does it hold up? **Yes, with one boundary condition that A-239's own wording correctly carries.** The claim is airtight *relative to candidate (ii)'s defining constraint* — a correction that consumes the shared model's post-merge line sets. `go_cover.py:64–73` merges executed-wins during parsing; a statement on a shared block boundary (proven real by the probe, per A-218's Rbrace analysis) is collapsed to "executed" before any downstream code runs, and no function of the merged sets can recover which block owned it. The one escape hatch — the adapter re-reading the raw profile itself — is excluded architecturally (it duplicates the parser and breaks A-006's format/language axis), not information-theoretically. A-239 says "no **post-merge** correction can recover it," which is exactly the right scope qualifier. No overclaim.

## A-242 (flat protocol) — faithful, count verified correct; one attribution nit

I recounted against the real code. The protocol (`base.py`) has 5 attributes + 7 methods. `resolve_mutation_targets` (`mutation.py:398–402`) consumes `excluded_dir_names`, `source_globs`, `is_test_path` on the R2 path; `generate_mutation_sites` is called at `mutation.py:578`; `name` keys the registry. So SQL needs 2 of the 7 methods and the attributes, and **exactly five methods are dead: `has_executable_code`, `normalize_coverage_key`, `statement_spans`, `inject_import_break`, `inject_uncovered_line`** — A-242's list is exactly right, and matches what I said in round 3. Two small precision notes: (a) `external_tools` and `requires_span_attribution` are listed as "needed," which is true only in the declared-constant sense — nothing in the shipped code consumes `external_tools` at all yet (verified: zero consumers outside docstrings; the preflight is still unbuilt, see Part 4), and `requires_span_attribution` is consulted only on the R1 path; (b) the prompt paraphrases A-242 as saying *I* overstated the deadness — the actual entry says "**A-238** overstated," which is the carver's own prior entry, and my round-3 text made the same correction A-242 makes. No misattribution in the record itself; the counts agree everywhere. The `GoAdapter.statement_spans`-returns-`None` distinction (correct-by-contract no-op vs. no-correct-value-exists) and the named reopen trigger both landed as recommended.

## A-243 (helpers widening) — faithful, verbatim

All three scope guards landed as stated: `MUTATION_DISCOVERY_FAILED` only, with `MUTATION_UNSUPPORTED` and `MISSING_EXTERNAL_TOOL` explicitly excluded and the reason for the asymmetry stated; the generic phrasing for P27's reuse; both layers plus the negative test requirement. The no-schema-change fact is correctly recorded (I re-verified: the helpers-correspondence rule exists only in `verdict.py:2024` and `verify.py:612`; the schema never had it). The helper-succeeded-but-run-failed-elsewhere sub-case is correctly left to P34's carve. Nothing narrowed or reinterpreted. Nothing has landed in code — correct, since the SQLAdapter doesn't exist yet.

---

# Part 2 — A-240 and A-241, verified empirically

## What I ran

I took the misreport test's own fixture (`r2_error_exec_failed_baseline_crashed.json`, baseline R0 = `ERROR/EXEC_FAILED`), forged the payload-free R2 claim to each status in A-240's list plus controls, and ran the real `verify_document`:

| Forged payload-free R2 claim | Shipped verifier |
|---|---|
| `ERROR/GIT_FAILED` | **accepted, zero failures** |
| `ERROR/UNREADABLE_ARTIFACT` | **accepted, zero failures** |
| `ERROR/MUTATION_DISCOVERY_FAILED` | **accepted, zero failures** |
| `NO_MEASUREMENT/EMPTY_COVERAGE` | **CAUGHT** by the re-derivation oracle |
| `BUDGET_EXCEEDED/LANE_TIMEOUT` | **CAUGHT** |
| `ERROR/FORMAT_MISMATCH` (control) | **CAUGHT** |
| `NO_MEASUREMENT/DIRTY_TREE` (control) | accepted (correct — truthfully co-occurable per A-175/A-178) |
| `FAIL/COMMAND_FAILED` | CAUGHT (the existing test's case) |
| `PASS` (payload-free) | refused by **reconstruction** ("schema: claim[R2]: PASS without a mutation payload") — the re-derivation oracle never runs |

## A-241: confirmed, exactly as recorded

The three accepted `ERROR/*` cases are precisely `_INDEPENDENT_R2_TERMINALS`' non-DIRTY/HEAD members (`verify.py:1103–1112`): the branch checks only that the status matches the closed vocabulary for the reason and never compares against the baseline. A-241's three-item list is correct, its mechanism description is correct, and its "found by driving, not reading" claim is borne out.

**Severity, honestly:** low today, and "record and move on" was the right call — with one addition. The forgery changes *diagnosis*, not gate outcome (both spellings are `ERROR`, exit 2; it doesn't even cross A-020's retry-policy boundary). No producer can emit the shape (the six independent terminals arise only while R0 passed, per the set's own documentation), no consumer reads artifacts today, and both model and verifier share the same information limit. What raises it later: the moment external artifact consumers exist (the comparer below would be the first), a forged-but-accepted foreign document becomes a real input. The addition: **the fix is subtler than A-241's framing implies, and my DIRTY_TREE control shows why** — a blanket verbatim-comparison over all six set members would be *wrong*, because `DIRTY_TREE`/`HEAD_CHANGED` legitimately co-occur with a non-PASS baseline (post-command refusals preserve the real R0 claim), and whether `BASE_IS_HEAD` can co-occur needs a `run_lane` control-flow read, not an assumption. The repair needs per-terminal reachability evidence — which is precisely why not improvising it in a doc commit was correct. Natural home: the same P34-era `verify.py` touch A-243 already schedules.

## A-240: the correction of A-237 stands; the replacement evidence does not

Three of A-240's empirical claims are wrong:

1. **"All five pass re-derivation as independent terminals" — false for two of five.** `EMPTY_COVERAGE` and `LANE_TIMEOUT` are not in the independent set; their misreports are caught by the verbatim comparison. The five-item list appears hand-assembled rather than read off `_INDEPENDENT_R2_TERMINALS`.
2. **"The oracle fires for PASS and FAIL" — false for PASS.** A payload-free R2 PASS is refused by reconstruction (`Claim._check_a_judged_status_carries_its_own_payload`) before stage 3 ever runs; the re-derivation oracle is *already* structurally unreachable for PASS, today, branches or no branches.
3. **"Closing the gap makes that oracle structurally unreachable" — false under the correctly-scoped branch set.** My recommended branches were R1-PASS/FAIL, **R2-PASS-only**, R3-PASS/FAIL/INCONCLUSIVE — R2-FAIL must stay payload-free-legal because that is A-116's own truthful propagation shape. I added exactly those branches to the schema in memory and swept every committed v5 verdict document in the repo (49 checked): **zero break** (the only six failures are P33 templates with `@STARTED@` placeholders, which fail the *unmodified* schema's timestamp pattern identically — pre-existing, template-by-design). The misreport test's forged `FAIL/COMMAND_FAILED` document **stays schema-valid** under the added branches, so the test survives untouched.

What evidently happened: the restored branches included R2-FAIL-requires-payload — overbroad, and refuting *that* set is not refuting the recommendation. Notably, there are zero committed payload-free R2 FAIL fixtures (I checked), so the overbroad branch produced no fixture red to flag the scope error — only the forged test doc went red, which was then read as the boundary defending itself.

**Is A-240's conclusion correct now?** As *doctrine*, it can still stand: "the schema owns refusal of impossible payloads, not requiredness of evidence" is a coherent boundary, every case is caught by model + verify, and the operator may simply prefer schema stability. But it now stands on preference, not measurement — the recorded justification is disproven, and by this project's own rules (A-232: a stated result is not evidence; paste the real output) that must be corrected in the record either way. There is a pattern worth naming without drama: A-237 was justified by evidence that didn't hold; A-240 corrected it and substituted evidence that also doesn't fully hold. Both times the error was measuring (or citing) something adjacent to the actual question. My probe output above is the artifact A-232 asks for.

**My recommendation:** record an A-240 correction, then reopen the enforcement question on its true cost, which has collapsed: the correctly-scoped branches break nothing (measured), the two conformance-vehicle fixes were already written once during the A-240 attempt, and A-236(b) established the in-place-tightening precedent. I now lean toward adding the branches — weakly before, less weakly now, because the counter-argument ("it forces deleting a real test") is gone. If the operator still prefers the doctrine boundary, that is a legitimate choice — but it should be recorded as a choice, not as a forced result.

---

# Part 3 — The Open section, item by item

Three entries are **resolved-but-not-marked** — the exact pattern the section's own A-O19 entry models handling correctly:

- **A-O14 — RESOLVED by P21.** `ERROR/OUTPUT_WRITE_FAILED` shipped (A-157/A-163/A-181); `errors.py:85` literally cites "(P21/A-O14/A-181)". The open question ("does write failure need a code, or is the uncaught OSError acceptable?") was answered: it got the code *and* the whole `VerdictOutput` reservation state machine. Mark it.
- **A-O15 — RESOLVED by P26.** The entry's subject, `attestation._changed_paths` with its `splitlines()` transport, **no longer exists**: A-211 replaced the changed-display-names approach with per-path `ls-tree`/`diff --quiet` queries under `--literal-pathspecs`, and I verified `attestation.py` today contains no `_changed_paths`, no `splitlines`, and executes no `--name-only` (the one grep hit is a docstring describing the old defect). The U+2028/C-quoting hazard is gone because the representation that carried it is gone. Mark it, crediting A-165/A-211.
- **A-O16 — RESOLVED by P21.** `exclusion_capability` ("reported"/"unavailable") is exactly the "sibling flag" candidate the entry names; `verdict.py:144–151` cites A-O16 as the thing it closes. The entry's own deadline ("decide before P25") was met by P21. Mark it.

The genuinely open ones, with opinions:

- **A-O01, A-O02 — stale-open, discharge them.** Both were P01-era questions; the trove has existed and the gate has run for 33 packages, and A-123/P24 settled the image question empirically (wheelhouse added without a rebuild). Zero-cost cleanup.
- **A-O03 (TS adapter) — open, but the row needs two updates.** First, its "and the lcov/Istanbul parsers" clause is half-shipped: `coverage_parsers/lcov.py` exists with tests since P03; only Istanbul JSON is missing. Second, the carved queue now contains **P32 "real Vitest format conformance"** — the row and the queue don't reference each other, so a reader can't tell whether P32 subsumes, partially covers, or is orthogonal to A-O03. My read: P32 is format-level conformance and A-O03's adapter question (shell out to `tsc`/`node` or stay text-only) remains genuinely open and correctly deferred until dstdns's webapp lane is in scope. When it opens, note Istanbul's `statementMap` is statement-precise — no Go-style oracle needed, which makes the TS adapter cheaper than Go, not another instance of it.
- **A-O04 (srdm) — open, and now the most actionable.** Part 5b below is effectively this item's analysis. My standing recommendation (packaging probe attached to P28) is unchanged and now code-verified.
- **A-O05 — open, correctly parked.** A-244 explicitly chose A-O06 over it; the budget-decision blocker stands.
- **A-O06 — well-maintained.** The A-244 acceptance banner is exactly how a live table should track a status change. This is the model the three stale entries above should follow.
- **A-O07 — open.** My opinion from round 3 stands: natural companion to the A-O06 package post-P32; both are "run the declared lane twice under one controlled variation." Don't carve it alone.
- **A-O08/A-O09 — open, correctly ordered (A-O08 blocked on A-O09).** But A-O09's blocking text is stale: "the artifact schema should be checked for room **before v1 freezes**" — five schema versions have shipped and the room-check never happened (a per-claim `enforcement` field would today be a v6, since `claim` is `additionalProperties: false`). That's fine — v-bumps are routine here now — but the row should say "will ride the first async producer's schema bump" instead of naming a v1 deadline that's five versions gone.
- **A-O10 — open, correctly consumer-pulled.** No change.
- **A-O11 — open, still genuinely blocked** (nyxloom's redesign is at CR-13a-next, short of the CR-14 trigger).
- **A-O12 — open, correctly assigned to ciu.** Worth one added cross-reference: B44 (gate identity, filed from dstdns two days ago) is adjacent evidence that provenance-of-what-actually-ran is the estate's live pain; when ciu's `worktree` verb materializes, both items likely resolve together.
- **A-O13 (mdt) — open, trivially resolvable.** One look at whether mdt has a testable surface. Do it whenever adoption order next matters; not before.

---

# Part 4 — Full audit, A-001 through A-244

I read every entry, including sessions 5–37 which I had only grepped before. The overall verdict first, because it's earned: **the supersession discipline is the best I have seen in a decision record.** The chains I traced end-to-end all hold: A-050→A-073, A-024→A-074, A-055→A-071, A-086→A-087→A-142→A-144→A-163, A-011/A-114→A-183, A-153→A-167, A-116's bucket shape→A-157/A-180, A-172→A-218, A-189→A-212, A-237→A-240. A-128's "unreachable pairs" were closed by A-141 *with* the audit moved. Against that baseline, here is everything I found that is actually stale — contradicted by later code or decisions with no recorded correction:

1. **A-240's evidence paragraph** — Part 2. The one that matters most, because it's three days old and load-bearing for a live boundary.
2. **A-129's mechanism clause.** It rules that `assay verify` "validates it against packaged schema v2 (**reusing `verdict.load_schema()`**, imported not reimplemented)." The shipped `verify.py` deliberately does the opposite — its docstring says `load_schema()` is *not* called; conformance is dataclass reconstruction, for the A-005 zero-runtime-deps reason. The contract half of A-129 (artifact validator, not lane runner) stands; the mechanism half was silently superseded by the implementation and never corrected by id. Risk: a future reader "restoring" `load_schema()` use in verify, citing A-129 in good faith. One supersession line fixes it.
3. **A-163's "P26 owns effective-PATH reachability" — silently transferred.** P26 merged as attestation/deadline hardening; I verified there is **no `MISSING_EXTERNAL_TOOL` producer anywhere in `src/`** (only the errors.py reservation, and `registry.py`'s docstring explicitly declining preflight machinery). The shipped `errors.py:111–115` comment says "RESERVED for **P27**." So ownership hopped P22→P26→P27, and the last hop is recorded nowhere — the exact A-072/A-147 shape (a deferral whose executor changed without a ruling). It's benign today (the state is unreachable; both adapters declare no tools) and P27's re-carve is the obviously right owner now (the statement-position helper is the first real external tool) — but the re-carve should claim it explicitly, and the record should note the hop.
4. **A-069 "built wheels version as 0.0.0" — superseded in fact by P24, uncorrected by id.** A-198/A-199 gave the gate a hash-bound setuptools-scm closure; gate/release builds now produce real SCM identities (clean tag → `1.2.3`, untagged → dev identity, no-VCS → the `0.1.0` fallback — meaning A-069's *other* claim, "fallback_version is declared but unexercised," is also no longer true). The `0.0.0` shape now survives only on the standalone-fixture path inside the gate image. A-124 built test logic on A-069's blanket claim; the devcontainer red I found in round 1 (now a skip) was this staleness surfacing. One qualifying line pointing at A-198/A-199 closes it.
5. **A-102 — conclusion intact, premise disproven, no forward pointer.** Its ruling (`requires_span_attribution=False`; the span path is never reached for Go) is still literally true of the shipped code, and its narrow claim (Go structurally lacks Python's vanishing-interior-line gap) remains correct. But its supporting frame — block expansion confirmed as trustworthy per-line classification — is the premise A-O19/A-217 demolished, and A-235/A-239 will change exactly the gating A-102 settled. A-234 bannered the *code files*; A-102 itself is the decision a P27 re-carve implementer would grep. One qualifier line ("see A-217/A-239; the False setting survives but the seam is being redesigned") prevents a confident wrong citation.
6. **Minor record hygiene, batched:** `errors.py:123`'s "(RESERVED for P22)" on `SNAPSHOT_LIMIT_EXCEEDED` is stale — P22/P23 landed it and fixtures exist (A-190). The session numbering collides: two "session 24" headers exist (2026-08-08 post-series review and 2026-08-10 "P24 JIT freeze," the latter sitting between sessions 31 and 32 — almost certainly a mislabel). A-107's committed Go canary profiles are stale per A-234, which names the file but doesn't amend A-107 — acceptable since A-234 is discoverable, listed here only for completeness.

Everything else I checked — including every ruling in sessions 5–18 against the current protocol/adapter/config code, the P20–P26 security rulings against `git.py`/`safeio.py`/`isolation.py`'s docstrings, and A-133's permanent-R0 self-lane against `assay.toml` — is still true of the codebase as it stands.

---

# Part 5 — Operator follow-ups

## 5a. The verdict comparer, worked concretely

All field names below are real — taken from the shipped schema and the committed `r2_pass.json` fixture shape I dumped above.

**Setup.** Lane `package`, two runs. *Prior* at commit `aaa…`, *current* at commit `bbb…`. Both declare `rigor = ["R0","R1","R2"]`; both end `outcome: "PASS"`, `exit_code: 0`.

Prior artifact (abridged to the load-bearing fields):

```json
{ "schema_version": 5, "lane": "package", "commit": "aaa…", "outcome": "PASS",
  "judgment": {
    "resolved": {"language": "python", "source_roots": ["pkg"], "base": "990…"},
    "r1": {"coverage_format": "coverage-py-json", "coverage_artifact": "cov.json",
           "fail_under": 100.0, "allow_excluded": false},
    "r2": {"jobs": 2, "max_mutants": 50,
           "operators": ["python:compare-swap","python:boolop-swap",
                         "python:bool-const-flip","python:falsy-swap"],
           "kill_attribution": "unattributed"} },
  "claims": [
    {"rigor":"R0","status":"PASS", …},
    {"rigor":"R1","status":"PASS","coverage":{
       "covered": 31, "changed_executable": 31, "pct": 100.0, "considered": 38,
       "exclusion_capability": "reported",
       "missing_lines": {}, "files_missing_coverage": [],
       "unclassified_lines": {}, "files_with_unclassified_lines": [],
       "excluded_lines": {}, "files_with_excluded_lines": []}},
    {"rigor":"R2","status":"PASS","mutation":{
       "candidate_count": 6, "total": 6,
       "killed": [ …6 mutant_outcome entries… ],
       "survived": [], "crashed": [], "budget_exceeded": [], "equivalent": []}} ],
  "evidence": [ {"source":"attested","key":"security-review","status":"PASS",
                 "verified_by_assay": false, "producer":"j.doe",
                 "attested_commit":"aaa…","reviewed_paths":["pkg/auth.py"]} ] }
```

Current artifact, same shape, different numbers: `r1.coverage` = `{covered: 2, changed_executable: 2, pct: 100.0, considered: 3}`; `r2` declares `operators: ["python:compare-swap"]` and `equivalence_artifact: "schema.sql.dump"`, and `mutation` = `{candidate_count: 7, total: 7, killed: 3, survived: [], equivalent: [ …4 entries… ]}`; the evidence entry still names `attested_commit: "aaa…"` (now an ancestor) and still PASSes because `pkg/auth.py` is byte-identical.

**Invocation and output:**

```
$ assay compare prior.json current.json
assay compare: lane "package", aaa1111 → bbb2222 (schema v5 both)

R1 changed-line coverage    PASS → PASS
  pct 100.0 → 100.0 (unchanged)
  evidence mass: changed_executable 31 → 2, considered 38 → 3
  ⚠ the floor held, but this run proved coverage of 2 lines where the prior proved 31
  missing lines: none → none (0 resolved, 0 new, 0 persistent)

R2 mutation                 PASS → PASS
  score 100.0% (6/6) → 100.0% (3/3)   [equivalent excluded from denominator, v5]
  buckets: killed 6 → 3, survived 0 → 0, equivalent 0 → 4
  ⚠ 4 of 7 attempted mutants were provably inert this run; the score's
    denominator shrank from 6 to 3
  policy drift: judgment.r2.operators narrowed 4 → 1 (dropped: python:boolop-swap,
    python:bool-const-flip, python:falsy-swap)

Evidence (attested, "security-review")   PASS → PASS
  attested_commit aaa1111 both runs: fresh at prior, now 1 commit behind HEAD
  (still current — every reviewed path byte-identical; will go STALE when
  pkg/auth.py changes)

3 advisories, 0 regressions in judged status.  exit 0
```

`--json` emits the same as a structured document; the human text is the default. The comparer is **diagnostic, never a gate** — exit 0 unless an input is unreadable or the two artifacts fail its own preconditions (same `lane`, same `schema_version` — a cross-version pair is refused with one version diagnostic, A-138's spirit; differing `commit`s expected, with the same-commit case explicitly supported as a rerun-comparison, which is A-O07's shape for free). Making it a gate would put policy in a tool whose whole value is having none; a consumer that wants a ratchet gate wraps the `--json` output.

**The worked catch — what neither artifact alone shows a reviewer:** both runs are `PASS`, `pct: 100.0`, exit 0, schema-valid, `verify`-clean. A reviewer reading either verdict sees a green gate at full coverage. Only the *pair* shows that (a) the coverage floor is now standing on a 2-line denominator where it stood on 31 — real fields: `coverage.changed_executable`/`considered`; (b) the mutation score's denominator collapsed by the `equivalent` bucket's surge — real fields: `mutation.killed`/`equivalent`, with the v5 exclusion rule applied; and (c) the declared kill policy quietly narrowed from four operators to one — real field: `judgment.r2.operators`, which is precisely the recorded-policy data P16–P19 fought to get *into* the artifact and which nothing currently reads back. One honest caveat belongs in the tool's own output: cross-commit comparison compares *different diffs*, so a falling denominator is evidence-mass telemetry, not by itself a defect claim — which is exactly why it should be an advisory, not a failure.

## 5b. Shipping without consumer-side Python

**What I checked in the actual codebase** (all verified this session): zero runtime dependencies and `requires-python = ">=3.11"` (`pyproject.toml`); the only packaged data file is the schema, read via `importlib.resources.files(__package__)` (`verdict.py:244–252`) — the one mechanism that works identically in a source tree, a wheel, and a zip; version via `importlib.metadata` with a `PackageNotFoundError` fallback (`__init__.py:19`); **no dynamic import machinery anywhere** — no `entry_points`, no `__import__`, no plugin discovery; the registry is explicit code construction; the only function-level imports are three first-party circular-avoidance imports (`mutation.py:800`, `config.py:1153`, `runner.py:1522`), all statically resolvable. This is about as packaging-friendly as a Python codebase gets, and that is A-005 paying out a second time — the constraint adopted to make the scratch-venv proof trivially offline is the same one that makes freezing trivial.

**What no packaging can remove:** `git.py` resolves the `git` binary from the caller-declared `PATH` and refuses to guess without one (`git.py:337–339`), and the lane's own argv needs the consumer's test toolchain. So the true consumer-side footprint is: *assay's form* + `git` + the project's own toolchain. Every option below only changes the first term.

**The options, ranked:**

1. **zipapp — the right light artifact.** A single `.pyz` needs only `python3 ≥ 3.11` in the container. Cost: near zero. One thing to verify in the probe rather than assume (I'm flagging this as informed expectation, not tested here): `importlib.resources.files()` over zipimport for the schema read — stdlib supports it, but the probe should execute `assay verify` from the `.pyz` to prove it, in this project's own paste-the-output style.
2. **Single self-contained binary (PyInstaller/Nuitka/PyOxidizer) — feasible precisely because of A-005, but not free, and I'd hold it.** No C-extension deps, no dynamic imports, one data file — the freeze itself is about as low-risk as this tooling gets. The real costs are estate-shaped, not technical: the freezer becomes a build dependency that P24's own discipline (hash-bound `--require-hashes` wheelhouse, A-198) would demand be pinned and closed the same way — PyInstaller's closure is much larger than five wheels; you inherit a glibc/arch matrix; and the entire release/verification grammar is wheel-first (A-200's manifest binds `{filename, version, sha256}` to a wheel and feeds pip's own hash mode — a binary needs a parallel manifest grammar and loses the pip-rechecks-what-it-opens property). Nothing in the codebase *fights* it; the distribution machinery would simply need a second, carved lane. Justified only if a consumer class appears where installing `python3` is genuinely impossible — and none exists in this estate, because every gate image is estate-owned.
3. **Go reimplementation — no, and the record itself is the argument.** It recreates the founding problem (§2's four-diverged-copies) one language later, against 17.7k lines of source, 2,377 tests, and a three-layer artifact contract that took five schema versions and six adversarial rounds to freeze — all of which would fork on day one. `covergate` is the cautionary tale *for* consolidation, not a precedent for rewriting: it exists because adoption was too hard, and it now carries a live defect (it silently skipped an srdm package's coverage). The one durable Go-shaped piece is already planned as the *helper* (P27/P29's statement-position oracle — real Go, compiled in the Go image, speaking a narrow protocol), which is the correct way for Go-native capability to exist without forking the judge.

**Recommendation, unchanged in direction and now code-verified in substance:** the container probe is the near-term answer — add `python3` plus the assay wheel (or the zipapp) to `tester-unified-go`, measured for image-size/pull-time cost, attached to P28 since that package already runs against srdm's real repository. The zipapp is the preferred artifact if image weight matters. Keep the single binary as a documented, deliberately-untriggered option with a named condition ("a consumer environment where python3 cannot be installed"), in exactly the style A-242 just used for its protocol-factoring reopen trigger. And close the loop in the record: whatever the probe measures should land as the A-O04 resolution, so the srdm question stops being open-by-default.

---

**One process note to end on.** Two of this session's three real findings (A-240's evidence, A-163's silent ownership hop) and both prior rounds' headline findings (A-237's evidence, the Go fixtures) are instances of a single failure mode: *a claim about what code or tests do, recorded without the output that would prove it, by someone confident enough not to run it.* A-232 already names the rule. The addition this round suggests: it applies to decision *reasons* — especially reverts — exactly as much as to carve receipts. Every claim in this report that could be produced by running something was produced by running it.

---

# Addendum 3 — round 4 (2026-08-11): ciu-synergy check and the A-237/A-240/A-241
# doctrine recommendation

> **Controller note.** This is Fable's last round for this wave — per the
> operator's direct instruction, carve/review reverts to the standard Opus
> xhigh pipeline (CR-opus-0) after this. Two asks this round: (1) whether
> ciu's newly-shipped features (S15.17-S15.20 KSM/exec-wrapper, S16
> worktree instances, S17 image provenance) answer any assay open decision
> or warrant an upstream ciu backlog entry; (2) an explicit pros/cons/
> recommendation — not a deferral — on whether to close the hollow-PASS/FAIL
> schema gap now that A-240's counter-evidence was shown wrong. Verified
> against HEAD `59a94473a27877bfb405cfad7ca59774456e4a85`.
>
> **Headline 1:** S17 (image provenance) closes half of A-O12 outright, and
> found A-O12's own text stale (its `declared_unverified` claim doesn't
> exist in assay's source). Drafted concrete substance for a ciu upstream
> backlog entry (`ciu provenance --json`) that would let assay build its
> first real Tier-2 `adjudicated` integration — also naming provenance as
> the leading candidate for A-O10.
>
> **Headline 2: recommends CLOSING the hollow-PASS/FAIL schema gap now**,
> ~75/25, not a coin flip — the shipped schema already enforces requiredness
> in three other places (NO_MUTANTS, MUTANT_LIMIT_EXCEEDED,
> ALL_MUTANTS_EQUIVALENT), so "leave it open for doctrine coherence" is
> defending a doctrine the artifact already contradicts. Recommends this as
> a small standalone carver-owned change before P34's dispatch, not folded
> into P34. This has NOT yet been decided by the operator as of this
> addendum — surfaced for a decision, not yet acted on.

HEAD verified at `59a94473`. Everything below was read from the actual SPEC sections and code — `deploy.py:556` (`verify_running_provenance`), `cli.py:376`, S15.17–S15.20, S16, S17 in full — plus fresh greps of assay's own surface where A-O12's wording made claims about it.

---

# Part 1 — ciu's new functionality against assay's open decisions

## S17 (image provenance) vs A-O12: closes ciu's half completely; assay's half was never built, and A-O12's own text misdescribes it

**What S17 delivers, verified:** `bake` stamps `org.opencontainers.image.revision` with a load-bearing `-dirty` suffix and stamps *nothing* when the revision is unknown; `verify_running_provenance` (`deploy.py:556–637`) refuses at **test** time when a running container's labelled revision differs from the commit under test, correctly instance-scoped so a sibling S16 worktree running a different commit isn't misread as staleness, with honest non-refusals (unlabelled skipped, dirty warns, absent-image not a mismatch). This is exactly the mechanism A-O12 said didn't exist — "the check needs `org.opencontainers.image.revision` stamped at bake time, which ciu does not do today (D7 lists it as a gap)." That gap is gone, and the check's fail-closed/test-time design is *better* than what A-O12 asked for.

**Why A-O12 still can't be marked fully closed:** two assay-side facts, both checked this session.

1. **A-O12's claim "assay records `declared_unverified`" is false.** That string appears nowhere in assay's source, schema, or design guide. The verdict artifact records the bare `scope` enum (`S0`–`S4`) and nothing about whether the WHERE was verified. The entry describes a field that was apparently planned and never built — a fourth instance of the stale-open-entry pattern (alongside A-O14/A-O15/A-O16 from round 4).
2. **Assay has no slot to carry a provenance result, and structurally cannot compute one itself.** This is the A-030 boundary doing exactly what it was designed to do: at S3/S4, assay runs *inside* the test container; the provenance question ("what image is this running container actually built from?") is answerable only from *outside*, via the docker daemon — ciu's side of the boundary. So assay can never make this a computed claim. The only honest carriers are the evidence arrays: and of the two, **adjudicated** — reserved since A-034/A-078 with zero integrations — is the natural fit ("a tool's verdict assay records but does not verify").

**The convergence worth naming:** this makes `ciu provenance` the best candidate answer to **A-O10** ("which Tier 2 integration is built first, and its threshold vocabulary — deferred pending a target tool, SAST vs SBOM"). Compared to either SAST or SBOM, provenance is in-estate, has a tiny closed vocabulary with *no threshold policy question at all* (the thing A-O10 is actually blocked on), and building it discharges another open decision (A-O12) at the same time. One integration, two open entries advanced.

**What ciu doesn't yet have that this integration needs — the backlog entry.** `verify_running_provenance` returns `None` or raises; its outputs are prose warnings and exit codes, and — the load-bearing gap — **the success path is silent**. For gate wiring (a consumer's script runs `ciu provenance` before `assay run`), that's fine. For *evidence* (the verdict artifact records that provenance was checked and what it found), it's insufficient twice over: assay must never parse prose (its own A-204 precedent: byte-copy, never interpret — an interpreting reader becomes a shared oracle), and "no refusal happened" is not the same fact as "checked and matched" (assay's A-025 doctrine: absence of an adverse signal is never recorded as a positive fact). So:

> **Draft substance for `ciu/KNOWN_ISSUES_TODO_BACKLOG.md`** (matching house style — mechanism, what's needed, why here; SPEC ID cited):
>
> *Title:* S17.3 — machine-readable provenance verdict, for downstream evidence consumers (assay).
> *Mechanism:* `ciu provenance --json [PATH|-]` (precedent: `ciu diagnose --json`, `cli.py:59`) emitting one closed, bounded JSON document alongside the existing exit-code behaviour: `{schema_version, instance: "<project>-<env_tag>", commit_under_test, tree_state: "clean"|"dirty"|"not-a-checkout", containers: [{name, image, labelled_revision|null, status: "match"|"mismatch"|"unlabelled"}], overall: "verified-match"|"mismatch"|"not-verified-dirty"|"not-verified-unknown"|"refused-no-identity"}`. **A verified match must be recorded, not silent** — the current success path returns without output, and downstream evidence needs the positive fact, not the absence of a refusal. The vocabulary must be closed and stable: the consumer (assay, per its closed-vocabulary discipline) will refuse members it doesn't know rather than guess.
> *Why it belongs in ciu, not assay:* the instance-prefix scoping and the label are ciu's own facts (S16/S17.1 — only ciu knows which running containers belong to this instance); assay runs *inside* the container at S3/S4, on the wrong side of the ciu/assay topological boundary (assay decision A-030), and must never shell out to docker; and parsing `ciu provenance`'s prose from a consumer script would couple two estate tools through unstable human-facing text.
> *What it unblocks downstream (context, not ciu's work):* assay's first Tier-2 adjudicated integration — a lane declaring `(adjudicated, "image-provenance")`, a bounded reader for this document (assay's existing `safeio` pattern), and the status mapping (`verified-match`→PASS, `mismatch`→FAIL, `not-verified-*`/`refused-*`→NO_MEASUREMENT-class). That is an assay package, resolving assay's A-O12 and answering A-O10; it is named here only so the vocabulary is designed once, for its real consumer.

Assay-side, correspondingly: A-O12 should be annotated (ciu half closed by S17; the `declared_unverified` claim stale; assay half re-scoped to "first Tier-2 integration, vocabulary per the ciu backlog entry"), and A-O10 annotated to name provenance as the leading candidate. Sequencing: post-ship-milestone material — it competes with A-244's accepted A-O06, and I would *not* displace A-O06 for it; but it's now the obvious second post-Go item, and cheap.

## S16 (worktree instances) vs A-O04: no connection — its real relevance is B44

S16 is checkout/instance lifecycle (`worktree add|rm|list`, per-worktree `ciu.env`, normative clean-before-remove ordering). It contains nothing about container toolchains, so it neither helps nor hinders A-O04 (python-in-srdm's-container). Its actual significance in this estate is that it is **B44's named wait-condition arriving**: B44 (gate identity, filed from dstdns two days ago) explicitly said "ciu is expected to grow a `worktree` verb owning the directory structure, which likely dissolves the inference problem — sequence this behind that rather than building an inference now." The verb now exists. Whether S16's owned directory structure actually dissolves nyxloom's gate-identity inference problem is a nyxloom/B44 question, not an assay one — but whoever owns B44 should be told its blocking condition has (at least partially) fired. Nothing for assay to file.

## S15.17–S15.20 (KSM shim / exec-wrapper / memory_profile) vs the container probe: mechanism exists in spirit, unsuitable in fact — and S17 strengthens my original recommendation

I checked the injection mechanisms honestly rather than dismissing on category: ciu *does* have a build-in-container-and-inject pattern (S15.17 compiles the shim in `gcc:13-bookworm` and bind-mounts the artifact; S15.20 re-states entrypoints around a wrapper binary). Could that inject python3 into a running srdm container without owning the image build? No, for two reasons, one technical and one doctrinal:

- **Technical:** the injection class is a single dependency-free object — S15.17's verify-before-use rule *requires* zero `DT_NEEDED` entries precisely because the artifact must load inside another process under either libc. A python3 runtime is the opposite shape: an interpreter binary plus a full stdlib tree plus loader/libc coupling. It doesn't fit through this door, and forcing it through a bind mount would be a fragile reinvention of "installing python," worse than either real option.
- **Doctrinal, and this is the stronger reason:** a runtime-injected toolchain is *ambient* — invisible to S17's provenance stamp and to assay's own identity discipline (V5-5's `helpers` array exists because "a symbolic toolchain reference makes a verdict unreproducible"; the P27 probe pinned image digests for the same reason). S17 actually resolves the question in the other direction: **bake python3 (or the assay zipapp) into `tester-unified-go`, and the toolchain is then covered by the stamped revision** — provenance-verifiable, pinned, reproducible. The container probe recommendation from round 3 stands, now with a stronger justification than it had: the probe should note that the resulting image is S17-stampable, which makes the assay-in-Go-image deployment *more* provenance-honest than any injection scheme could be.

Nothing to file upstream here; rule S15.x out of the A-O04 path explicitly so nobody re-derives this.

---

# Part 2 — The doctrine call: close the hollow-PASS/FAIL gap, or leave it open

## Pros of closing now

1. **It restores the *simpler* doctrine, because the narrowed one is factually false about the shipped schema.** This is the decisive argument, and it's not philosophical. A-237/A-240's narrowed doctrine says the schema owns "refusal of impossible payloads, **not requiredness of evidence**." The shipped schema *already owns requiredness of evidence in three places*: the `NO_MUTANTS` branch **requires** `mutation` (line ~1527), `MUTANT_LIMIT_EXCEEDED` requires the exact sentinel payload (~1550), `ALL_MUTANTS_EQUIVALENT` requires `mutation` (~1598) — each added by a review round because its absence was a forgery window (A-136, A-163, A-228). So "leave it open for doctrine coherence" defends a doctrine the artifact contradicts today; keeping it honestly would need a *third* reformulation — "the schema owns requiredness keyed on reason codes but never on statuses" — which is gerrymandered around an accident of which review round found which hole. Closing the gap makes A-182's original one-sentence doctrine ("every locally expressible rule") true again and deletes the exception that has now consumed three decisions and two failed justifications.
2. **It protects the only consumer population the schema exists for, in the direction that matters.** A-029's whole design is that consumers validate with the shipped schema and never link against assay. The hollow PASS is the *gate-defeating* direction — a false green — the model's own docstring calls it "the strongest form of exactly the lie P16 exists to catch." Today that lie is caught only by consumers who run assay's own verify. The comparer (accepted roadmap direction) will be the first real schema-consumer; it should not be the discovery vehicle for this gap.
3. **The measured cost is as close to zero as schema work gets.** No v6: A-236(b) set the in-place-tightening precedent three days ago, for exactly this situation (no truthful producer emits the refused shape). Zero committed documents break — measured, 49 swept, output pasted in round 4. The two conformance fixture vehicles were already repaired once during the A-240 attempt. The misreport test survives untouched under the correctly-scoped branches — also measured.
4. **This is the cheapest moment there will ever be.** Pre-consumer, pre-adoption, precedent fresh. Tightening later — with consumers' handwritten test documents in the wild — is when it becomes a genuinely breaking change needing a v-bump and a migration. This is A-153's "last cheap moment" logic applied to a contract instead of package ids.

## Cons of closing now

1. **It's real, verified work in a mid-flight wave:** roughly four new branches, a negative fixture per branch, two vehicle repairs, a full-suite run to A-232 standard, and a supersession decision. Small, but it must be done properly — the failure mode of tossing it into a doc commit is the one A-241 just correctly refused.
2. **A second withdrawal from the A-236 in-place-tightening account.** Documents valid yesterday become invalid today under the same `$id`. Once is a precedent; twice is a pattern; a third would start to look like unversioned contract churn. If closing, the decision should state the rule explicitly: in-place tightening is legal only when a sweep proves zero truthful documents affected, with the sweep output pasted.
3. **Residual scope-error risk** — the overbroad-R2-FAIL mistake is exactly how the last attempt went wrong. Mitigation is mechanical: the branches must mirror `Claim._check_a_judged_status_carries_its_own_payload` and `_MUTATION_ONLY_REASON_CODES` *verbatim* — R1 ∧ status∈{PASS,FAIL} ⇒ `coverage`; R2 ∧ status=PASS ⇒ `mutation`; R2 ∧ reason=`MUTANTS_SURVIVED` ⇒ `mutation`; R3 ∧ status∈{PASS,FAIL,INCONCLUSIVE} ⇒ `canary` — and *nothing else*, because payload-free R2 FAIL is A-116's own truthful propagation shape.

## Pros of leaving open

Zero implementation work now; no second precedent withdrawal; no live exposure (model + verify catch every case in the real pipeline, and the schema-only consumer population is currently empty); fewer schema conditionals.

## Cons of leaving open

The big one: **it isn't actually the zero-work option.** A-240's evidence is disproven and must be corrected in the record regardless (A-232 is unambiguous about this), and an honest leave-open also requires re-stating the doctrine so it stops misdescribing the shipped schema — that's most of the writing cost of closing, purchased without the protection. Plus: the contract permanently permits a state no honest producer can emit, in the gate-defeating direction; and this corner is empirically a churn magnet — two of my review rounds plus A-236/A-237/A-240 have all orbited it, and an open exception with two failed justifications invites the next reviewer to orbit it again.

## Recommendation

**Close it.** Not 50/50 — roughly 75/25, and the 25 is execution risk, not doubt about direction. The argument that decides it for me is #1: the choice is not "tighter schema vs. simple doctrine," it is "make the doctrine true again vs. write a third, more contorted doctrine to excuse the gap." Once the "it breaks a real test" counter-argument fell to measurement, leaving the gap open stopped having a principled defense and kept only an effort defense — and the effort difference between honest-leave-open and close is small and one-time.

Shape: a small standalone carver-owned change **before P34 dispatch**, not folded into P34 — it touches the schema, which P34 otherwise doesn't, and A-219(c)'s own seam rule says contract changes don't ride adapter packages. Contents: the four branches above, one negative fixture each, the two vehicle repairs, full suite run with output pasted, and one supersession decision that (a) corrects A-240's evidence with the measured table, (b) restores A-182's original scope, and (c) states the in-place-tightening rule from con #2.

The one condition that would flip me: **if the operator already expects a v6 in P34's near wake** (say, real SQL runs force the `per_mutant_seconds` trigger named in SCHEMA-V5-DESIGN, or adjudicated-evidence detail fields for the provenance integration above force schema room) — then fold the branches into that v6 instead of tightening v5 a second time, and record only the A-240 correction now. That's a sequencing preference, not a change of direction.

## Does A-241 change the calculus?

It sharpens it rather than changing it, and the two do **not** share a design — but they share an inventory, and that's worth one decision naming both. The full question is "what may a payload-free claim assert?", and it splits cleanly along the layer boundary:

- The **hollow-green** lie (PASS/FAIL without the payload that judged it) is the *locally expressible* slice — claim-internal, schema's job under the restored doctrine. That's the gap to close now.
- The **wrong-reason-adverse** lie (A-241: `ERROR/GIT_FAILED` claimed against an `ERROR/EXEC_FAILED` baseline) is irreducibly *cross-object* — it needs the R0 sibling, which Draft 2020-12 cannot reach — so it belongs to verify's re-derivation, exactly where A-241 already assigns it.

Two facts make the split principled rather than convenient: the failure directions differ (hollow-green defeats the gate; A-241's misreports keep the outcome adverse and the exit code identical — they corrupt diagnosis, not verdicts), and the layers' consumers differ (schema-only consumers vs. verify-running consumers). Closing the schema slice makes the layering claim exact: after it, every payload-free-lying case is either schema-refused (green lies) or verify-refused (reason lies, once A-241 lands), with the model backstopping both. So: schema branches now; A-241's fix stays scheduled with P34's verify touch per its own ownership note — with the round-4 caveat repeated, because it's the part that could go wrong: the fix needs per-terminal reachability evidence, since `DIRTY_TREE`/`HEAD_CHANGED` legitimately co-occur with a non-PASS baseline (A-175/A-178) and `BASE_IS_HEAD`'s co-occurrence needs a `run_lane` control-flow read, not an assumption. A blanket verbatim rule over all six independent terminals would refuse truthful artifacts — the same class of scope error, one layer over.
