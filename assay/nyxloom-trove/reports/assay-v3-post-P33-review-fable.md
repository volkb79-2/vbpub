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
