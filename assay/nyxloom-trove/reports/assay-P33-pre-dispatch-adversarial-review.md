# Assay P33 pre-dispatch adversarial specification review

> **Reviewer:** fresh Opus xhigh child forked from `CR-opus-0` (A-216), commissioned by the controller.
> **Review date:** 2026-08-11
> **Repository HEAD at review:** `495b71f3dabe582694a5536c084637a08fdb366d` (clean tree, verified with `git rev-parse HEAD` / `git status --porcelain`)
> **Handoff:** `nyxloom-trove/handoffs/assay-P33-verdict-schema-v5.md`
> **Declared `input_revision`:** `b03555d79227ef7eb76eaf7f851c2896968fa455`
> **Carve report:** `nyxloom-trove/reports/assay-P33-JIT-CARVE.md` (frozen at the same commit)
> **Assets:** `nyxloom-trove/carve-assets/P33/` (6 files + README; all six recorded sha256 digests re-computed and **match**)
> **Authoring doctrine:** `nyxloom/reference/AUTHORING.md`, revision `2026-08-08-r5`
> **Method:** the exact `Pre-dispatch adversarial handoff review` prompt from AUTHORING.md, copied from the live file and byte-compared against the controller's transcription — **identical, no transcription error**.

## Result first

**NOT READY.** Eleven blocking defects, of which four are *mechanically unsatisfiable* requirements: the package as carved cannot reach a green gate without either editing a locked carve asset (forbidden by A-197/A-222) or editing a file in `scope.forbid`. Three of the four were invisible to the carver's own probe because `verify_document`'s version short-circuit (A-138) masks every downstream check on a `schema_version: 5` document.

Machine shape is fine and is not the problem: the frontmatter validates against `nyxloom/src/nyxloom/schemas/handoff-frontmatter.schema.json` with zero errors, lint rule L1 (filename stem == `id`) passes, and `depends_on: [assay-P26-attested-evidence-cli-hardening]` resolves to a real handoff. Every defect below is semantic.

The design (`SCHEMA-V5-DESIGN.md`) is largely sound and V5-1 is a genuine v4 bug fix — confirmed independently: `tests/fixtures/verdicts/r2_pass.json`, `r2_pass_with_judgment.json` and `r2_fail_mutants_survived.json` are all `R0,R2` with `judgment: {r2}` and therefore record no language, no source roots and no comparison commit today. The defects are in the *carve*, not the design.

---

## (1) Blocking ambiguities

### B1 — `migrate_v4_to_v5.py --check` cannot exit 0 after the work, and it is demanded twice

The implementation packet states the check "must exit 0 against the committed asset **both before and after your work**", and work item 1 repeats it. The script resolves its v4 source as the live shipped schema:

```python
V4 = HERE.parents[2] / "src" / "assay" / "schemas" / "verdict.schema.json"
...
if v4["properties"]["schema_version"].get("const") != 4:
    print("REFUSING: source schema is not v4 ...")
    return 2
```

Work item 1 installs v5 at exactly that path. Reproduced against a simulated post-implementation tree:

```
$ python3 nyxloom-trove/carve-assets/P33/migrate_v4_to_v5.py --check
REFUSING: source schema is not v4 -- this transform is v4-specific and re-running it against v5 would double-apply.
check exit=2
```

(Pre-implementation it exits 0, as recorded.) The script is a locked asset the implementer may not edit (A-197). The demand is therefore impossible; the implementer's only compliant move is `BLOCKED`.

**Repair (carver-owned):** either restate the requirement as "exits 0 at the input revision, and is not re-run afterwards", or teach the script a `--source` argument / a committed v4 snapshot so the delta stays auditable post-migration. This is a proven defect in a locked asset, so A-197 permits the carver to correct it with a new decision and hash epoch.

### B2 — the keystone locked template contradicts shipped `judge_mutation` precedence and will be REJECTED by the v5 verifier

`expected/sql-r2-v5-template.json` records `status: FAIL` / `reason_code: MUTANTS_SURVIVED` on its R2 claim, and `outcome: FAIL` / `exit_code: 1` at the top level, with a **non-empty `budget_exceeded` bucket**. A-117's precedence (crashed > budget_exceeded > survived), shipped verbatim in `assay.mutation.judge_mutation`, makes that unreachable. Re-derived against the real functions:

```
buckets: killed=1 survived=1 equivalent=1 crashed=0 budget_exceeded=1  total=4 candidate_count=4
shipped judge_mutation on these buckets   -> ('BUDGET_EXCEEDED', 'LANE_TIMEOUT')
SQL template's own R2 claim records       -> ('FAIL', 'MUTANTS_SURVIVED')
rollup([R0 PASS, re-derived R2])          -> BUDGET_EXCEEDED
SQL template's own top-level outcome/exit -> FAIL 1
```

`verify.py:976` calls `judge_mutation(baseline, claim.mutation)` and appends a failure on disagreement; `_check_outcome_agrees_with_rollup` fails independently. So work item 7 ("prove both templates validate through the installed wheel's own verifier") and probe expectations 3/4/5 inverting are **unsatisfiable**. The template is locked; `src/assay/mutation.py` is in `scope.forbid`. Second `BLOCKED`.

**Why the carver did not see it:** probe expectation 4 is satisfied entirely by the version diagnostic. Live output:

```
PASS  sql-r2-v5-template.json is REJECTED by the current raw verifier  [1 failure(s): schema_version 5 is not this verifier's version 4: a verdict]
```

`verify_document` returns that single diagnostic and stops (A-138, `verify.py:1019-1033`). No internal-consistency check ever ran against either template.

**Repair:** empty the `budget_exceeded` bucket (and re-do the arithmetic), or change the recorded status to `BUDGET_EXCEEDED`/`LANE_TIMEOUT` with `exit_code: 4`. Either way the template must be re-hashed, and the probe must gain an expectation that runs the *v5* verifier over the templates rather than relying on a version short-circuit.

### B3 — `judgment.resolved.base` is required in a shape where the product has no value for it

V5-1 makes `judgment.resolved` required whenever `judgment` is present, with `base` required inside it ("The full resolved comparison commit, never a symbolic ref", `minLength: 1`). But:

- `config.JUDGE_FIELDS_BY_RIGOR["R3"] == ("language", "source_roots", "canary")` — **`base` is not a required judge field for R3**;
- `runner._resolve_base_commit` returns `None` when `judge.base` is absent (`runner.py:1263`);
- an `R0,R3` import-break lane is legal (A-192 requires R1 only for `uncovered-line`);
- and three committed fixtures are in exactly that shape — `r3_pass.json`, `r3_fail_canary_survived_unexpected_pass.json`, `r3_inconclusive_canary_inconclusive.json`, all `declared_rigor: ["R0","R3"]` with `judgment` keys `['r3']`.

The design's justification for hoisting `base` is R2-specific ("mutation targets changed lines … so it consumes the same resolved comparison commit R1 does") and does not cover R3-without-R1. The implementer must invent a value for both the producer and those three migrated fixtures. That is the shadowing-default hazard AGENTS.md §"Defaults and fallbacks are hazards" names, and it is a NOT-READY trigger on its own.

**Repair options (a product decision, not implementer discretion):** make `base` optional inside `judgment_resolved` and required only when `r1` or `r2` is present; or make `resolved` itself conditional on `r1`/`r2`; or add `base` to `JUDGE_FIELDS_BY_RIGOR["R3"]`. Pick one and record it.

### B4 — `judgment.r2.kill_attribution` is required with no declared source

V5-4 makes `kill_attribution` a **required** member of `judgment_r2`. `JudgmentR2` is built exclusively from lane config (`runner.py:1479-1483`):

```python
judgment_r2 = JudgmentR2(
    jobs=lane.judge.mutation.jobs,
    max_mutants=lane.judge.mutation.max_mutants,
    operators=lane.judge.mutation.operators,
)
```

`MutationConfig` has no `kill_attribution` field and no decision creates one. The carve report defers producers for `equivalence_artifact` and `kill_signal_artifact` to P34 — correct, both are optional — but `kill_attribution` is required, so P33's own producer must emit a value for every real R2 run. The implementer must either hardcode a literal (a shadowing default for a fact the lane should own) or invent a lane-grammar field (name, requiredness, default, validation). Both are externally visible decisions.

### B5 — invariant 5 (helper correspondence) has an unobservable antecedent

> "A claim whose payload was produced with an external helper requires the matching `helpers` entry."

Nothing in the artifact links a claim to a helper: `helpers` entries carry `role`/`tool`/`resolved_path`/`identity`, and claims carry no helper reference. The antecedent "was produced with an external helper" is not a fact the verifier can read from artifact bytes. As written the check is unimplementable; any implementation is vacuous. This also makes O1's second clause ("records the helper that produced its mutation sites") uncheckable by any oracle other than the hand-written template.

**Repair:** either define the linkage (e.g. `role` → declared-rigor correspondence: a `mutation-sites` entry requires an R2 claim carrying a `mutation` payload, and *vice versa* for an adapter declaring `external_tools`), or delete invariant 5 from P33 and hand it to P34 with the adapter that makes it reachable — matching A-142/A-144's precedent for deferring a check until a producer makes the state reachable.

### B6 — O3's "excluded from the mutation score's numerator and denominator" names a quantity the artifact does not contain, and the exclusion is unobservable

There is no `score` field anywhere in v5. Status is the only observable, and status comes from `judge_mutation` — which reads only `total`, `crashed`, `budget_exceeded`, `survived`, lives in `src/assay/mutation.py`, and is **forbidden**. So an implementation that ignores `equivalent` entirely and one that "excludes it from the score" are byte-identical in every artifact and every test. O3's second clause cannot fail.

Worse, the consequence is the project's own founding bug: a run whose mutants are *all* equivalent (killed 0, survived 0, equivalent 3, total 3) walks `judge_mutation` to its final `return Outcome.PASS, None`. A run that proved nothing about the tests renders **PASS** with an undefined 0/0 score — the 0/0-is-100% shape A-026/A-035 exist to remove, reintroduced one layer down. No terminal is defined for `killed + survived == 0` with a non-empty `equivalent` bucket.

### B7 — work item 5 contradicts shipped A-139 behaviour and misreads A-183

> "A Go or SQL R2 lane must load and then render payload-free `INCONCLUSIVE/MUTATION_UNSUPPORTED` per A-183, never a load-time refusal and never green."

`cli._built_in_registry` registers **Python only** ("Python is registered at R1, R2 AND R3 and nothing else", `cli.py:20`). `cli._resolve_declared_adapters` consults the registry for every declared level above R0 *before anything executes* and `refuse_lane` renders `ERROR/BAD_LANE_CONFIG` (A-139). So today a Go **or** SQL R2 lane gets a pre-execution refusal, not `MUTATION_UNSUPPORTED`. A-183 says so itself: *"The Go R2 producer remains unreachable through the registry."*

Making work item 5 true requires registering `go` and `sql` in `cli.py` (which has **no scope status at all** — see D2), touching `src/assay/adapters/go.py` (explicitly **forbidden**), and creating `src/assay/adapters/sql.py` (does not exist, not in scope, and is P34's deliverable). A-221's premise — that leaving `go:*` empty "would have made a Go R2 declaration fail at load, while the design guide and A-183 both say Go renders `INCONCLUSIVE/MUTATION_UNSUPPORTED`" — is therefore wrong about the current product: a Go R2 lane already fails at load, for a different reason, and populating the operator enum does not change that.

### B8 — the v5 schema still identifies itself as version 4

```
v5 $id : 'urn:assay:schema:verdict:4'
v5 properties.schema_version.const : 5
```

The transform's rewrite is dead code — it tests `"v4" in $id`, and the id is `urn:assay:schema:verdict:4`, which contains no literal `v4`:

```python
d["$id"] = d["$id"].replace("v4", "v5") if "v4" in d.get("$id", "") else d.get("$id", "")
```

So the shipped v5 contract self-identifies as v4. And the one existing oracle that reads it asserts the wrong value is correct — `tests/test_verdict_schema_is_packaged.py`: `assert proc.stdout.split()[0] == "urn:assay:schema:verdict:4"`. That test is **not in the migration manifest**, so a manifest-following implementer leaves both the defect and the assertion that blesses it in place. This is a locked-asset defect (A-197 carver correction) plus a manifest gap.

### B9 — `judgment.minProperties: 1` is silently dropped; the design document does not say so

`transform()` executes `judgment.pop("minProperties", None)`. v4 required `judgment`, when present, to carry at least one of `r1`/`r2`/`r3`; v5 accepts `{"resolved": {...}}` alone. `SCHEMA-V5-DESIGN.md` V5-1 says only that `judgment` "gains `resolved`… and adds `required: ["resolved"]`" — the widening is nowhere in the specification the handoff calls frozen, and no cross-object invariant closes it (`_check_judgment_matches_claims` only checks `rN`-present-iff-claim-judged, in both directions).

Consequently it is **undefined whether the producer emits `judgment` when only `resolved` is known** — e.g. `runner.py:1567-1569` currently builds `judgment` only if some tier is non-None, and `runner.py:1596-1604` sets it to `None` when refusal empties every tier. Under v5 an implementer could reasonably emit `{"resolved": ...}` in both cases. Two implementations, both passing every named oracle, producing different artifacts — and the second records resolved language/source-roots/base for a run that judged nothing, which is the A-025/A-136 family (a recorded fact nothing witnesses).

### B10 — `kill_signal` on a non-killed entry is undefined

The schema puts `kill_signal` on `mutant_outcome`, i.e. on entries in every bucket. The design says "Present on every killed entry when `kill_attribution` is `'declared'`; absent otherwise." The handoff's invariant 4 says "`declared` requires `kill_signal_artifact` and a `kill_signal` on every `killed` entry; `unattributed` forbids both, **on every bucket**." Neither states whether, under `declared`, a `kill_signal` on a *survived*/*crashed*/*budget_exceeded*/*equivalent* entry is legal. An implementer must decide; O4's negative names only the `unattributed` direction.

### B11 — which `base` fills `judgment.resolved` on a lane declaring both R1 and R2

v4 recorded `judgment.r1.base` from `resolved_base_holder[0]`, the value resolved *inside* the snapshot by `evaluate_r1` (`runner.py:1374`), while the higher-rigor path also computes `resolved_base` against the consumer repository before the snapshot exists (`runner.py:1669-1673`). v5 has **one** `base` shared by R1 and R2. The handoff does not say which is authoritative, nor that they must be asserted equal. Same question for `source_roots`: `judgment.r1.source_roots` is deliberately taken from `lane.judge.source_roots` (declared, project-relative, A-049) and **not** from `relocated_lane`, whose roots are absolute scratch paths (A-149). Inside the snapshot block the relocated lane is the object in hand; an implementer building one shared `resolved` there will reach for it, and leak scratch-absolute paths into the artifact. Nothing in the handoff names this.

---

## (2) False-PASS attacks

For each oracle, one plausible wrong implementation that passes the proposed tests.

**O1 — R0,R2 representability + helper provenance.**
*Attack 1 (declared/effective namespace):* build `judgment.resolved.base` from `lane.judge.base` — the declared string — instead of the resolved merge-base. Every existing fixture declares `judge.base` as a full 40-hex SHA, and `resolve_base` on a full SHA returns that same SHA, so declared and resolved are indistinguishable in the whole suite. The schema is `{"type": "string", "minLength": 1}`, so a branch name validates. This is **A-143 verbatim**, and A-143's own repair (one fixture declaring a symbolic ref and expecting the resolved OID) is not required anywhere in P33.
*Attack 2 (helpers):* never populate `helpers` at all. P33 registers no helper-producing adapter, invariant 5's antecedent is unobservable (B5), and the only artifact exercising `helpers` is a hand-written template. O1 passes with a producer that has no helper support whatsoever.

**O2 — closed per-language vocabulary.**
*Attack:* implement the config-load check correctly but implement the artifact check as `operator.split(":")[0] in {"python","go","sql"}` rather than `== judgment.resolved.language`. Every migrated fixture is Python-with-python-operators, so nothing distinguishes the two. Add a second: implement the rule for `judgment.r2.operators` and not for `mutant_outcome.operator` (the existing A-148 check already constrains payload operators to the *declared* set, so a document whose declared set is itself cross-language slips through both).
*Also:* `judge.language` is an **opaque non-empty string** in `config.py` (`config.py:737-741`, and the module's own comment "`judge.language` stays an opaque string"). Nothing closes the language set at any layer, so "closed per language" is only enforced for the three prefixes that happen to exist. Under A-182's ownership rule an enum is locally expressible in the schema and is not there.

**O3 — equivalence pairing and score exclusion.**
*Attack:* implement the pairing only. The exclusion half has no observable (B6), so the "wrong" implementation and the right one are identical artifacts. Every test passes; an all-equivalent run reports PASS.

**O4 — attribution consistency.**
*Attack:* under `unattributed`, check that `kill_signal_artifact` is absent but do not check `kill_signal` on entries — or check only the `killed` bucket. A fixture pair (one `declared`-and-complete, one `unattributed`-and-bare) passes. Work item 3 already demands per-clause breaks; the oracle text does not enumerate the clauses, so the implementer decides how many there are.

**Cross-cutting attack on the whole package — the migration itself.**
Migrate all 90 files by mechanical rewrite, run the suite, ship. Every fixture moved together, so nothing in the suite can distinguish a *correct* migration from a merely *consistent* one. The handoff's own "Package-specific test emphasis" names the right three negatives; none of them is a locked, frozen, carver-authored expectation, so the implementer authors the tests that are supposed to catch the implementer. See verdict A.

---

## (3) Missing implementation-packet content

1. **No decided value or rule for `judgment.resolved.base` on an R0,R3 lane** (B3).
2. **No source for `judgment.r2.kill_attribution`** — no lane field, no default, no ruling (B4).
3. **No terminal for `killed + survived == 0`** with a non-empty `equivalent` bucket (B6). The reason vocabulary is closed (A-050); an implementer needing a code must stop and ask, so this must be answered before dispatch.
4. **No rule for `kill_signal` on non-`killed` entries** (B10).
5. **No statement of which resolution fills the single shared `base`, nor which spelling fills `source_roots`** (B11).
6. **No statement of whether `judgment` is emitted when only `resolved` is known** (B9), and no invariant restoring v4's "at least one tier" guarantee.
7. **No reproducible scan command for `migration-manifest.json`.** The manifest records `"scan": "files under assay/ with schema_version 4, judgment.r1 hoisted keys, or a bare (unqualified) operator name"` as prose. An independent scan of tracked files finds 103 matches; the manifest's three buckets cover 96 of them. The seven not covered are `docs/DESIGN-GUIDE.md`, `nyxloom-trove/SCHEMA-V5-DESIGN.md`, the migration script itself, `decisions.md`, and three fixture files whose only hits are docstring prose (`tests/fixtures/mutation/python/sample.py`, `tests/fixtures/mutation_exec/python/pkg/checks.py`, `tests/fixtures/mutation_exec/python/tests/test_checks.py`). Separately, `tests/test_verdict_schema_is_packaged.py` needs migration (B8) and is in no bucket. The `excluded_build_artifact` bucket lists 7 files under `build/lib/**` that are **untracked** (`git ls-files build` returns nothing), which shows the scan ran over the carver's working tree rather than the index — an implementer in a fresh worktree will not have them at all.
8. **No owner for the doc drift `DESIGN-GUIDE.md` will acquire.** §11 and §12's worked TOML example declares `operators = ["compare-swap","boolop-swap","bool-const-flip","falsy-swap"]`. After P33 that exact example is a config-load refusal. `docs/DESIGN-GUIDE.md` is in `scope.forbid` with no successor named — the A-133/A-146 "prose declaring a capability must not drift from the real one" rule, unowned.
9. **A-222's own second clause is unexecuted.** It requires that where a locked acceptance suite asserts v4 shape, "the suite's oracle moves into P33's own v5 acceptance material and the v4 suite is retired, with the carve report naming what coverage moved where." No work item does this, the carve report names nothing, and P33 has no v5 acceptance material to move anything into. This is the A-072 shape — a ruling that reached `decisions.md` but not the handoff.

---

## (4) Scope / dependency defects

**D1 — `gate/python/qualify_topos.py` is required by work item 6 and is outside `scope.touch`.** Work item 6: "Migrate every path in `migration-manifest.json`'s `implementer_owned` bucket — 90 files." Checking all 90 against the frontmatter globs, exactly one falls outside: `gate/python/qualify_topos.py` (reason `judgment.r1-hoisted-keys`). It is tracked. This is DOCTRINE §3's forbidden-needed-file failure, and the handoff's `escalate_if` does not even cover it — only the generic BLOCKED rule does. A guaranteed wasted dispatch cycle.

**D2 — `src/assay/cli.py` and `tools/tester-unified-gate.sh` have no scope status.** The handoff states, at lines 176-179, that P27's defect was leaving `go_cover.py` "in neither `touch` nor `forbid`" and that "**every load-bearing file in this handoff has a status for that reason**." That claim is false for at least two load-bearing files: O1's observable is *"the installed CLI's own verifier…"*, work item 5 requires registry behaviour that lives in `cli.py`, and the registered gate script invokes locked acceptance material that P33 breaks (D3).

**D3 — P33 breaks the registered `tester-unified` gate through a locked asset nobody has given a status.** `tools/tester-unified-gate.sh:231` runs `nyxloom-trove/carve-assets/P26/test_acceptance.py` from the installed wheel, and that suite does, at lines 602-613 and 651-661:

```python
expected = _substitute_template(HERE / "expected" / template, {...})   # *-v4-template.json
assert verify_document(expected) == []
assert actual == expected
```

After P33, `verify_document` is the v5 verifier and the template carries `schema_version: 4`, so the first assert fails on the version diagnostic; `actual` (v5, with `judgment.resolved`) never equals `expected` either. `carve-assets/P26/test_acceptance.py` is a locked asset (A-214: implementers and reviewers may not edit it), it is **absent from the migration manifest entirely**, its four `expected/*-v4-template.json` siblings *are* in the locked bucket that A-222 freezes, and `tools/tester-unified-gate.sh` is not in scope. There is no in-scope move that leaves the gate green. Third `BLOCKED`.

**D4 — `input_revision` names a commit where the carve assets do not exist.** `input_revision: b03555d7…` is the design commit; the assets landed in `b6f0b3bf`. `git ls-tree -r b03555d7 -- assay/nyxloom-trove/carve-assets/P33/` returns nothing. An implementer branching from the declared input revision has no schema to install, no manifest, no templates and no probe. (The body's "Environment setup" says "From fresh main", which contradicts the frontmatter.) Per A-176/A-187/A-206/A-214 the anchor should be the commit *containing* the JIT carve report and its assets.

**D5 — `src/assay/verify.py` (in scope) depends on `src/assay/mutation.py` (forbidden).** `verify.py:90` is `from .mutation import judge_mutation`, and the R2 re-derivation is built on it. Every question about how `equivalent` affects a judged status therefore crosses the forbid boundary (B2, B6).

**D6 — roadmap drift.** `nyxloom-trove/handoffs/README.md` carries the operator-required execution-order callout, and it is correct and prominent: *"Execution order: P20–P26 (done) → P33 → P34 → [ship] → P27 (resumed) → P28 → P29 → P30 → P31 → P32"*, with "Numbers are identity, not sequence" and the dependency chain repeated below the table. Two stale spots remain: the section heading still reads "Current pre-adoption queue: P20–P32", and the P33 row still says "`SCHEMA-V5-DESIGN.md` landed; JIT freeze pending" though the freeze landed in `b6f0b3bf`. Cosmetic, but the P20–P26 rows record their merge/freeze commits and this one does not.

---

## (5) Corrected oracle / fixture matrix

### Requirement-to-oracle traceability, corrected

| requirement | carved oracle | status after review | required repair |
|---|---|---|---|
| V5-1 hoist `language`/`source_roots`/`base` | O1 | **broken** — unreachable for R0,R3 (B3); `base` provenance undefined (B11); `minProperties` widening unspecified (B9) | decide the R3 rule; pin the base source; restate or restore the "at least one tier" guarantee |
| V5-2 per-language closed operators | O2 | partial — config half sound; artifact half attackable (§2); `language` itself unclosed | add the `mutant_outcome.operator` × `resolved.language` negative; rule on whether `language` is closed |
| V5-3 `equivalent` bucket | O3 | **hollow second clause** (B6) | drop the unobservable score clause or add an observable; define the 0/0 terminal |
| V5-4 kill attribution | O4 | partial — no producer source (B4); non-killed-bucket rule undefined (B10) | rule both |
| V5-5 helper provenance | O1 (second clause) | **unimplementable** (B5) | define the linkage or defer to P34 |
| 90-file migration | *none* | uncovered by any oracle | see verdict A |
| v4 rejected by version | test emphasis only | sound | keep, and make it a locked expectation |

### Pairwise axes

`declared_rigor` {R0 | R0,R1 | R0,R2 | R0,R1,R2 | R0,R3 | R0,R1,R3} × `judgment` {absent | resolved-only | +r1 | +r2 | +r3} × `language` {python | go | sql | unregistered} × `judge.base` {absent | symbolic | full SHA} × `kill_attribution` {declared | unattributed} × `equivalence_artifact` {absent | declared} × `equivalent` bucket {empty | non-empty} × `helpers` {absent | present | present-without-a-corresponding-claim} × input `schema_version` {4 | 5} × mutation buckets {killed-only | survived | crashed | budget | equivalent-only | mixed}.

### Combined-axis fixtures (the six that break a convenient implementation)

**CA1 — R0,R3 + no declared `judge.base` + `judgment.r3` present.** Exposes B3. Today's three R3 fixtures are exactly this shape and have no migration answer. Must terminate in a *decided* way, not an invented OID.

**CA2 — R0,R2 + `judge.base` declared as a branch name + `judgment.resolved.base` asserted equal to the resolved 40-hex, with `language: "python"` and a `sql:drop-check` in `judgment.r2.operators`.** Combines A-143's declared-vs-resolved trap with O2's cross-language rule in one document. The convenient implementation (record the declared string; check the prefix against a fixed set) passes every single-axis fixture and fails this one.

**CA3 — R0,R2, `kill_attribution: "unattributed"`, no `equivalence_artifact`, buckets = {killed: 1 carrying a `kill_signal`, equivalent: 1}.** Two independent invariant violations in one artifact (O4's forbid clause and O3's pairing). Must produce two distinct failures, not one — the per-clause-break requirement of work item 3, made observable.

**CA4 — R0,R2 all-equivalent: `killed: []`, `survived: []`, `equivalent: [3]`, `total: 3`, `candidate_count: 3`, `kill_attribution: "unattributed"`, `equivalence_artifact` declared.** Exposes B6. Under the current forbidden `judge_mutation` this renders `PASS`. Whatever the ruling, this fixture must witness it.

**CA5 — the post-migration gate composite.** Install v5, then run: `migrate_v4_to_v5.py --check`, `carve-assets/P26/test_acceptance.py` from the installed wheel, and `assay verify` over both P33 templates. B1, B2 and D3 are *only* visible here; every one of them is invisible to any test run against the pre-implementation tree, which is why thirteen green probe expectations did not catch three unsatisfiable requirements.

**CA6 — higher-rigor lane (R0,R1,R2) run inside a snapshot, in a repo where `project_root != repo_top` (assay inside vbpub).** Assert `judgment.resolved.source_roots` records the declared project-relative spelling, never the relocated absolute scratch paths, and that the single `base` equals the pre-snapshot resolution. This is A-145/A-149's family — twice already, a copy-based orchestration has been wrong about paths in a way no fixture could see — and v5's single shared `resolved` object is a fresh opportunity for it.

**Plus the three the handoff already names correctly** (v4 refused by version; v5 missing `judgment.resolved` refused; cross-language operator refused). All three are right and none of them is currently locked.

---

## (6) Verdict

**NOT READY.**

Four requirements are mechanically unsatisfiable in scope (B1 post-work `--check`; B2 the locked SQL template versus shipped `judge_mutation`; D1 an implementer-owned path outside `scope.touch`; D3 the registered gate's locked P26 acceptance suite). Two oracles cannot fail as written (B5 helper correspondence; B6 score exclusion). Five externally visible decisions remain for the implementer to invent (B3 `base` on R0,R3; B4 `kill_attribution`'s source; B6 the 0/0 terminal; B10 `kill_signal` outside `killed`; B11 which `base`/which `source_roots` spelling). AUTHORING's own criterion — *"Mark the handoff NOT READY if any externally visible decision, interface, example, bound, refusal, or proof source remains for the implementer to invent"* — is met several times over.

None of this touches the design. `SCHEMA-V5-DESIGN.md`'s five changes are well argued, V5-1 is a real v4 bug fix (independently confirmed against three committed `R0,R2` fixtures), and A-221's operator catalogue is a faithful transcription rather than an invention. The defects are concentrated in the freeze: the assets were validated against the *pre-implementation* tree only, where `verify_document`'s version short-circuit hides every semantic check, so no one has yet observed the state P33 actually produces.

---

## Direct verdict on A — the absent locked acceptance suite

**It is a real gap, and a locked suite should be authored — but the carver's stated reason for omitting it is only half right, and the decisive argument is different from the one being debated.**

What is right: the dominant failure mode for a 90-file migration genuinely is "every fixture moved together", and a suite written against the same migrated shapes would inherit the interpretation it is meant to check. The three named negatives — a v4 artifact refused by version, a v5 artifact missing `judgment.resolved`, a cross-language operator — are correctly chosen precisely because no migrated fixture can satisfy them. That instinct is sound and should survive.

What is wrong, and why it decides the question:

1. **The existing acceptance signal is weaker than the report claims.** Of the thirteen probe expectations, three are satisfied by the version number alone. Live: expectation 4 for *both* templates is `[1 failure(s): schema_version 5 is not this verifier's version 4]`, and expectation 8 ("an existing v4 artifact is REJECTED by v5 — breaking, as intended") is `[1 error(s): ['schema_version']: 5 was expected]`. A purely additive change with a bumped `const` would produce exactly that. The report's claim that "a migration that were merely additive would show neither" holds for the SQL template's 13 schema errors and does not hold for the verifier or the reverse direction.

2. **The masking is not incidental — it is why three blocking defects survived the carve.** B1, B2 and D3 are all post-implementation-state facts. Every one of them is invisible to any assertion made against a tree whose shipped schema is still v4. A locked suite that ran against a *simulated post-implementation* tree would have caught all three before dispatch; that simulation costs a temporary directory, which is how I found them.

3. **A-222 already obliges P33 to have v5 acceptance material**, and it does not exist: "the suite's oracle moves into P33's own v5 acceptance material and the v4 suite is retired, with the carve report naming what coverage moved where." With D3 live, this is not optional bookkeeping — it is the only mechanism by which the registered gate stays honest across the version bump.

**So: author the suite, but author it against the post-implementation state, not against migrated fixtures.** That answers the common-mode worry directly — the suite's expectations are about a tree the implementer has not yet produced, derived from the locked schema and the shipped forbidden modules, so they cannot be back-fitted to whatever the implementer happens to write. Minimum contents I would require as a reviewer:

- the three already-named negatives, frozen rather than delegated;
- `assay verify` (v5) accepting **both** locked templates — the check that currently cannot pass (B2), witnessed red now and required to invert;
- `migrate_v4_to_v5.py`'s post-work contract, whatever B1's repair turns it into;
- CA1 (R0,R3 without a base), CA3 (double-violation), CA4 (all-equivalent), CA6 (relocated source roots);
- an artifact-identity assertion tying `$id`, `schema_version.const` and `VERDICT_SCHEMA_VERSION` together, so B8 cannot recur;
- an explicit statement of what coverage moves out of `carve-assets/P26/test_acceptance.py` and where it lands.

The reviewer-authored combined-axis test the handoff asks for remains required on top of that; it is not a substitute for the suite. As the carve report invites: **yes, please author it.**

## Direct verdict on B — does required `judgment.resolved` break a legitimate R0-only artifact?

**The narrow claim is correct. The cited witness does not witness it. And the check stopped one case short of the case that actually breaks.**

*Correct, verified directly rather than accepted:* `judgment` is **not** in v5's top-level `required` array (confirmed: v4 and v5 both require exactly `schema_version, assay_version, lane, commit, outcome, exit_code, started, ended, claims, evidence`), and `runner._prepare_outcome` constructs a `Judgment` only when at least one tier is non-None:

```python
judgment: Judgment | None = None
if judgment_r1 is not None or judgment_r2 is not None or judgment_r3 is not None:
    judgment = Judgment(r1=judgment_r1, r2=judgment_r2, r3=judgment_r3)
```

A genuine R0-only run therefore emits no `judgment` key, and making `resolved` required *inside* `judgment` cannot break it. V5-1 is safe for R0-only.

*The witness is the wrong document.* `expected/missing-tool-v5-template.json` is `declared_rigor: ["R0","R1"]`, not R0-only. What it actually witnesses is a different (also true) fact: when R1 renders `NO_MEASUREMENT`, no `judgment.r1` is built, so no `judgment` object exists. The README's gloss — "Only `schema_version` changes, which is itself the evidence that V5-1 touches nothing an R0/R1 refusal needs" — is true of that document and is not evidence about R0-only. No committed asset covers R0-only; `tests/fixtures/verdicts/r0_pass.json` does, and it is in the implementer-owned bucket, so the evidence for this claim will be produced by the implementer rather than frozen by the carver.

*The case that does break is one over.* An `R0,R3` import-break lane **does** emit a `judgment` (tier `r3` only), and `judgment.resolved` then requires a `base` that neither the config nor the runner has — `base` is absent from `JUDGE_FIELDS_BY_RIGOR["R3"]` and `_resolve_base_commit` returns `None`. Three committed fixtures are in that shape. That is B3, and it is the real answer to the question the carver asked.

*One further consequence found while checking:* dropping `judgment.minProperties` (B9) makes `{"resolved": {...}}` alone valid, so the inverse of the question is now open too — whether a producer may emit `judgment` carrying only `resolved` for a run that judged nothing. v4 made that unrepresentable; v5 permits it and no invariant forbids it.

---

*Reviewer note: this file was written, not committed. Git and merge mechanics belong to the controller under A-216.*
