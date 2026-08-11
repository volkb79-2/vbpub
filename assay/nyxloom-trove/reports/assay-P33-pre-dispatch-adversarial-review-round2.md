# Assay P33 pre-dispatch adversarial specification review — ROUND 2

> **Reviewer:** fresh Opus xhigh child forked from `CR-opus-0` (A-216), commissioned by the controller.
> **Review date:** 2026-08-11
> **Repository HEAD at review:** `147592e07c7770b45b1df375ba529e0f6804c7fc` (clean tree, verified with `git rev-parse HEAD` / `git status --porcelain`)
> **Handoff:** `nyxloom-trove/handoffs/assay-P33-verdict-schema-v5.md` (re-carved; read from disk, not from any cached copy)
> **Declared `input_revision`:** `7a774d57b41033e0f3de84cd5c2bb188f3cc401b`
> **Assets:** `nyxloom-trove/carve-assets/P33/` — **all ten recorded sha256 digests recomputed and match**; asset bytes verified unchanged since the declared anchor (`2e42d7f4`/`147592e0` touched only prose).
> **Authoring doctrine:** `nyxloom/reference/AUTHORING.md`, revision `2026-08-08-r5`
> **Method:** the exact `Pre-dispatch adversarial handoff review` prompt from AUTHORING.md, read from the live file and byte-compared against the controller's transcription — **identical, no transcription error**. Round 1's verdict was read *after* forming my own attack list, and every claimed fix was re-derived from primary sources rather than accepted.

## Result first

**NOT READY.** The re-carve is a large, genuine improvement: I independently confirm that **eleven of the seventeen round-1 defects are properly fixed**, that the locked suite reproduces **17 failed / 2 passed** exactly as claimed, and that its differential-negative construction really does defeat the version short-circuit that hid round 1's defects. B1, B2, B7, B8, B9, B10, D1, D2, D4, D5 and the manifest gap are closed, verified by re-derivation.

Three findings block dispatch anyway, and the first is the same disease as round 1's D3, one package over:

1. **The registered gate still breaks.** `gate/python/qualify_topos.py` — which the gate runs at line 249, whose success marker is a `die` condition — does whole-document equality between a live artifact and **two locked v4 templates in `carve-assets/P25/expected/`**, which the manifest itself places in `locked_carver_owned` and `scope.forbid` bars. There is no in-scope repair. P33's materials mention this file exactly once, and only to fix its *scope status*.
2. **The new terminal was never propagated to the layers that constrain its three siblings.** `MUTATION_UNSUPPORTED`, `NO_MUTANTS` and `MUTANT_LIMIT_EXCEEDED` each get an explicit reason/payload/rigor conditional in the locked schema. `ALL_MUTANTS_EQUIVALENT` gets none — so a payload-free, non-R2 `ALL_MUTANTS_EQUIVALENT` claim validates, which is verbatim the forgery branch 7's own description says it exists to close. The schema is locked byte-for-byte, so the implementer cannot fix it.
3. **Two externally visible decisions remain unmade** — what a `declared` lane emits when P33 ships no `kill_signal` producer, and what a `helpers` entry with `role: "executable-code"` corresponds to (2 of a closed 3-value vocabulary are ruled).

Plus one large, unacknowledged coverage loss: retiring P26's module from the gate drops **18 of its 24 tests**, only 4 of which are coupled to v4 artifact shape at all.

Machine shape is fine and is not the problem: frontmatter validates, L1 passes, `depends_on` resolves, and the scope↔manifest agreement is real — I re-ran it mechanically: **92/92 implementer-owned paths fall inside `scope.touch` and none matches `forbid`.**

---

## (1) Blocking ambiguities

### R2-B1 — `ALL_MUTANTS_EQUIVALENT` is the only mutation-only reason code with no constraint in any layer that owns one

The locked v5 schema constrains every sibling terminal explicitly (`$defs.claim.allOf`, verified by reading the committed asset):

| branch | reason code(s) | `then` |
|---|---|---|
| 6 | `MUTATION_UNSUPPORTED`, `MUTATION_DISCOVERY_FAILED` | payload **forbidden**, `rigor` const `R2` |
| 7 | `NO_MUTANTS` | payload **required**, `rigor` const `R2` |
| 8 | `MUTANT_LIMIT_EXCEEDED` | payload required, `rigor` const `R2`, exact sentinel shape |
| — | **`ALL_MUTANTS_EQUIVALENT`** | **absent** |

Three consequences, all provable from the locked bytes:

- **(a) The cheapest forgery of the new terminal validates.** `claim.allOf[4]` forbids a `mutation` payload *outside* R2; nothing requires one *inside* it for this code. So `{"rigor":"R2","status":"INCONCLUSIVE","reason_code":"ALL_MUTANTS_EQUIVALENT"}` with the payload deleted passes the schema. Branch 7's own description names this exact attack — *"A payload-free `NO_MUTANTS` is the forgery this closes"* — and A-136 is the decision that closed it. Reintroducing it inside the change that exists to fix a lossiness problem is the same shape A-223(d) cites for the PASS bug it does fix.
- **(b) It is not bound to R2.** An R1 or R3 claim may carry `INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT` — a canary claim reporting that all mutants were inert. Every sibling is pinned with `"rigor": {"const": "R2"}`; `MUTANT_LIMIT_EXCEEDED` is additionally rigor-gated in the model (`verdict.py:1412`). Nothing gates this one anywhere.
- **(c) The model layer is unnamed too.** `verdict.py:148` — `_MUTATION_ONLY_REASON_CODES = frozenset({MUTANTS_SURVIVED, NO_MUTANTS})` — is A-136's construction-time refusal set. `ALL_MUTANTS_EQUIVALENT` belongs in it and in the reasoning about `_INDEPENDENT_R2_TERMINALS` (`verify.py:827`). Work item 7 names `errors.py`, `judge_mutation` and the bucket arithmetic, and nothing else.

**Why this blocks rather than being an implementer detail:** A-182 assigns *locally expressible* conditionals — "reason/payload conditionals inside one object" — to the shipped JSON Schema, and this is one, in the identical form of three that already exist. The schema is a locked asset; `--check` re-derives it from `migrate_v4_to_v5.py`, and `test_shipped_schema_is_byte_identical_to_the_locked_asset` freezes it. The implementer physically cannot add branch 9. This is a proven defect in a locked asset, so A-197 lets the carver correct it — transform + schema + re-hash + README table.

**Related, same root cause (the fifth bucket was not propagated):** branch 8 pins `killed/survived/crashed/budget_exceeded` to `maxItems: 0` for the limit sentinel and **omits `equivalent`**; `$defs.mutation.description` still reads *"**FOUR** identity buckets"* while `required` now lists five; and `verdict.py:1421`'s error text still says *"four empty buckets"*. The first is a locked-asset defect; the last is in-scope but unnamed.

### R2-B2 — `kill_signal_artifact` becomes declarable in P33, and a lane that declares it cannot produce a valid artifact

Work item 6 adds `kill_signal_artifact` to `MutationConfig` and derives `kill_attribution = declared` from its presence. Invariant 4 then requires *"a `kill_signal` on every `killed` entry"*. **P33 ships no producer for `kill_signal`** — the carve report says so itself (*"No `equivalence_artifact` or `kill_signal_artifact` producer … P34 is the first package that produces one"*).

So a consumer lane that declares the new field and kills one mutant yields an artifact its own model refuses to construct and its own verifier rejects. I confirmed the mutation sub-table is parsed and closed, not opaque, so the field is genuinely declarable once added (a lane declaring `operators = ["sql:drop-check"]` today is refused with `'judge.mutation.operators' names unknown operator(s)`).

The implementer's three options are all externally visible decisions nobody has made: emit killed entries without `kill_signal` (self-inconsistent artifact), invent a reader for the artifact (a bounded-safe-input contract on the A-210 scale, plainly not in P33), or refuse the declaration until P34. `equivalence_artifact` has no equivalent hazard — its pairing rule permits a declared artifact with an empty bucket — which is exactly why this one needs saying.

### R2-B3 — the `helpers` `role` vocabulary is closed at three values and invariant 5 rules two of them

`role` is `["statement-positions", "mutation-sites", "executable-code"]`. Invariant 5 and A-223(c) define a correspondence for `mutation-sites` (⇒ R2 claim with `mutation`) and `statement-positions` (⇒ R1 claim with `coverage`). **`executable-code` has no rule.** Is it unconstrained, or does it require an R1 claim (`has_executable_code` feeds coverage evaluation), or an R1-or-R2 claim?

The handoff's own test constraints make this the project's named standard: *"a vocabulary needs a rejected value per language, not one rejection standing for all three."* The same sentence applied to `role` answers this finding. The locked suite exercises `mutation-sites` only; neither of the other two roles appears in any template or test.

### R2-B4 — `judgment.resolved.base` is required-if, never forbidden-unless

A-223(a) and the locked `judgment.allOf[1]` express `r1|r2 ⇒ base`. I verified the **if** direction is correct and complete across all six rigor combinations (see appendix B). The **only-if** direction does not exist: `judgment_resolved.properties` carries `base`, `additionalProperties: false` bars only unknown keys, and no invariant forbids it. An r3-only `judgment` carrying a `base` validates.

I checked whether the product can reach that state and it cannot — I probed the real loader: a lane declaring `rigor = ["R0","R3"]` with `judge.base` is refused with *"declares rigor ['R0','R3'], which reads none of judge.{base} — so that configuration is inert"* (A-062). **So this is not a producer bug; it is a hole in the independent verifier**, which A-182 exists to make trustworthy against foreign documents. The carver asked me to check "base iff r1|r2"; the honest answer is that it is `if`, not `iff`, and the missing half is one `if/then/not` branch away.

---

## (2) False-PASS attacks

One plausible wrong implementation per oracle that still passes the proposed tests.

**O1 — `judgment.resolved` representability.**
*Attack (A-143 verbatim, still live):* build `resolved.base` from `lane.judge.base` — the **declared** string — rather than the resolved merge-base. A-223(f) pins the rule in prose; **no fixture discriminates it.** Every locked template substitutes `@BASE_OID@` with a 40-hex string, and `resolve_base` on a full SHA returns that same SHA, so declared and resolved are indistinguishable in the entire locked suite. I grepped: `symbolic`, `branch name` and `A-143` appear **nowhere** in the handoff, the locked suite, or the assets README. A-143's own ruling is explicit — *"the fixture must make resolved and declared genuinely different, or it proves nothing"* — and this package, which collapses two base resolutions into one field, is exactly where it bites.

**O2 — closed per-language vocabulary.**
*Attack A (work item 5's own pinning test is non-discriminating):* work item 5 requires "a test pinning exactly that … for both `go` and `sql`", where "that" is the `_resolve_declared_adapters` refusal. But a config-layer refusal (an implementation that hardcodes the vocabulary to `python:*` and rejects `language = "sql"` at load) and the adapter-layer refusal (A-139/`refuse_lane`) **both surface as `ERROR/BAD_LANE_CONFIG`**. The two are distinguishable only by artifact emission — `refuse_lane` renders a complete verdict with one claim per declared level, while a load failure emits none (A-181). The work item does not say which observable is required, so the naive test passes under an implementation that makes the whole `go:*`/`sql:*` vocabulary undeclarable.
*Attack B:* implement the artifact-side prefix rule for `judgment.r2.operators` and not for `mutant_outcome.operator`. The locked cross-language test flips `resolved.language` on a document whose *declared* set is also `sql:*`, and A-148's pre-existing check already ties payload operators to the declared set — so one rule can cover for the other. No fixture carries an operator in the payload that is absent from the declared set.

**O3 — equivalence pairing and the all-inert terminal.**
*Attack:* add the branch to `judge_mutation` and stop there — omit it from `_MUTATION_ONLY_REASON_CODES`. Every locked test passes (they all validate complete documents), and a payload-free `ALL_MUTANTS_EQUIVALENT` remains forgeable (R2-B1(a)). Note the round-1 hollowness *is* fixed for the branch itself: I confirmed `verify.py:976` re-derives R2 status through `judge_mutation`, so an implementation that ignores `equivalent` renders `PASS` and fails `test_ca4_all_equivalent_is_inconclusive_not_pass`. The residue is the payload-presence half.

**O4 — attribution consistency.**
*Attack:* implement only the bucket rule. The single locked kill-signal test injects a `kill_signal` into the `survived` and `equivalent` buckets — which the **schema** now refuses on its own, so the test passes with *no verifier code at all*. Nothing locked exercises `declared` without `kill_signal_artifact`, `declared` with a killed entry missing its `kill_signal`, or `unattributed` with a `kill_signal` on a **killed** entry (the one bucket the schema deliberately leaves open, because that clause is cross-object). Three of invariant 4's five clauses have no discriminating fixture, in the package whose work item 3 demands *"per clause, not per function"*.
*On `test_ca3`:* it asserts `len(failures) >= 2`, and its own mutation breaks bucket arithmetic (it moves an `equivalent` entry into `killed` without adjusting `total`), so ≥2 is reachable from *arithmetic + pairing* while the attribution clause is unimplemented. It does not prove what it says it proves.

**O5 — gate retirement.**
*Attack:* do exactly what work item 8 says. The gate stops running 24 tests and starts running 19, of which the carry-forward reproduces **one** of P26's oracles. O5's observable is fully satisfied. See R2-D2.

**Cross-cutting — the producer is never exercised at v5.**
Every one of the 19 locked tests validates a **hand-written document**. Nothing in the locked suite runs `assay run` and inspects what it emitted. The gate's self-hosted lane is permanently R0-only (A-189) and therefore emits **no `judgment` at all**, so it cannot witness `resolved`. That leaves `qualify_topos.py` as the only producer-side v5 evidence in the registered gate — and R2-D1 is that it breaks. **A correct verifier with a wrong producer passes this package's entire locked acceptance material**, which is precisely the axis A-143, A-145 and A-149 were each written after.

---

## (3) Missing implementation-packet content

1. **What P33 emits for `kill_signal` when a lane declares `kill_signal_artifact`** (R2-B2) — or a ruling that the field waits for P34.
2. **The correspondence rule for `role: "executable-code"`** (R2-B3).
3. **The propagation list for the new reason code** (R2-B1): the schema conditional, `_MUTATION_ONLY_REASON_CODES`, the `_INDEPENDENT_R2_TERMINALS` reasoning, and the sentinel's bucket enumeration. Work item 7 names `errors.py`, `judge_mutation` and the arithmetic only.
4. **What `compare_complete_artifact` compares against after v5** (R2-D1) — the single largest gap; there is currently no answer that does not require editing a locked asset.
5. **What replaces P26's 18 shape-independent oracles** (R2-D2), and whether partial retention (deselecting the 4 template-coupled tests plus `test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it`) is preferred to retiring the module.
6. **A fixture in which declared and resolved `base` genuinely differ** (§2, O1) — A-143's own required shape.
7. **The `only-if` half of the base rule** (R2-B4).
8. **Which observable distinguishes the two possible `BAD_LANE_CONFIG` refusals** for work item 5 (§2, O2 attack A).

---

## (4) Scope / dependency defects

### R2-D1 — the registered gate breaks at the P25 qualification phase, and there is no in-scope repair *(blocking; same class as round 1's D3)*

`tools/tester-unified-gate.sh:249` runs `gate/python/qualify_topos.py`, whose success marker is required or the gate calls `die`. That harness:

- `qualify_topos.py:51` — `_EXPECTED_ROOT = _PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P25" / "expected"`
- `qualify_topos.py:715` — `normalized["judgment"]["r1"]["base"] = "@BASE_OID@"` (v5 moves `base` out of `judgment.r1`)
- `qualify_topos.py:740-742` — `expected = json.loads(template.read_text()); if normalized != expected: raise QualificationError("the complete v4 artifact differs from the locked hand template")`
- called for both common-semantics scenarios at `:1009-1018` against `pass-v4-template.json` and `missing-v4-template.json`

I read both templates directly: `schema_version: 4`, `declared_rigor: ["R0","R1"]`, `judgment: {r1: {language, source_roots, coverage_format, coverage_artifact, fail_under, allow_excluded, base}}` — the exact hoisted shape v5 abolishes. `schema_version` is **not** among the fields `normalize_artifact` normalizes, so whole-document equality fails on the version alone, and again on the restructured `judgment`.

Both templates are in the manifest's `locked_carver_owned` bucket (work item 9: *"Do not touch `locked_carver_owned`"*), and `nyxloom-trove/carve-assets/**` is in `scope.forbid`. `qualify_topos.py` is `implementer_owned`, so the implementer may edit the *reader* — but the only edits that work are (a) hand-writing a v4→v5 template transform inside a gate harness, with no locked expectation and no reviewer-visible correctness criterion, or (b) editing the locked template. (a) is an unfrozen proof source; (b) is forbidden. `escalate_if`'s second clause fires, so a competent implementer will correctly `BLOCKED` — a guaranteed wasted dispatch cycle, which is DOCTRINE §3's named most-expensive authoring defect.

**Why the re-carve missed it:** the manifest's scan looks for *shape patterns in file contents* (`"schema_version": 4`, hoisted keys, bare operators, the urn) plus one hand-added pattern, `runs-P26-v4-acceptance`, for the single instance round 1 named. It never asks **who consumes a locked v4 artifact at gate time**. `qualify_topos.py` was caught only as a file to migrate (`why: ["judgment.r1-hoisted-keys"]`), and "migrate the file" is not the job. I re-ran my own scan for `carve-assets/P25/expected` and it matches only the manifest itself, because `qualify_topos.py` builds that path from `/`-joined components — the same reason a content scan cannot find this class at all.

The general form is worth stating once: **a version bump breaks every frozen expectation any live code compares against, not only the ones that live in a test file.** The correct closure is an inventory of consumers of locked v4 artifacts, not another pattern in the scanner.

### R2-D2 — retiring P26's module from the gate drops 18 of its 24 tests, and only 4 are coupled to v4 shape *(blocking)*

I attributed every test in `carve-assets/P26/test_acceptance.py` (1014 lines, 24 tests) by whether its body touches a template, `verify_document`, `schema_version` or `judgment`:

- **4 genuinely v4-coupled:** `test_all_structural_and_aggregate_bounds_precede_every_git_call`, `test_cli_emits_the_complete_hand_authored_v4_artifact`, `test_cli_preserves_independent_malformed_missing_and_current_evidence`, `test_attestation_timeout_is_atomic_and_does_not_run_a_failing_command`.
- **2 incidental mentions:** the asset-hash test and `test_runner_binds_evidence_batch_to_lane_source_before_any_work`.
- **18 entirely independent of artifact shape**, and they are the expensive ones: `test_generic_git_expiry_kills_the_complete_group_and_preserves_the_terminal` (A-212's witnessed descendant-held-pipe hang), `test_generic_git_overflow_also_kills_its_complete_process_group`, `test_bootstrap_timeout_launches_no_substantive_git_command`, `test_annotated_tag_object_cannot_peel_into_an_attested_commit`, `test_git_helpers_treat_metacharacters_as_identity_not_pathspec`, `test_all_missing_batch_still_observes_expiry_inside_atomic_loading`, `test_missing_later_path_outranks_an_earlier_stale_directory`, `test_record_grammar_rejects_duplicate_json_members_before_git`, `test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget`, and nine more.

P33's carry-forward (`test_p26_attestation_shapes_survive_v5`) reproduces exactly one of these oracles: that four attestation **documents** still validate. I verified it is satisfiable — all four P26 templates are `declared_rigor: ["R0"]` with **no `judgment` key**, so an in-memory `schema_version` bump really does yield a valid v5 document. But the suite docstring's justification — *"nothing else in P26's suite asserted artifact shape"* — is a true statement that silently substitutes *shape coverage* for *coverage*. Eighteen behavioural oracles, several of them security boundaries this project paid for with witnessed incidents, leave the registered gate with no replacement and no mention.

The cheaper and more honest repair is partial retention: keep invoking P26's module with the 4 template-coupled tests deselected, plus `test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it` (which asserts the gate runs *that* suite and emits `attestation-hardened`, and would necessarily fail after work item 8). `--deselect` lives in the gate script, which is now in scope; no locked byte moves. If the carver prefers full retirement, the 18 oracles need a named destination — that is A-222's own second clause applied honestly.

### R2-D3 — the anchor predates two of its own corrections *(minor)*

`input_revision: 7a774d57` is correct for the assets: I verified `2e42d7f4` and `147592e0` touched only prose, so every carve asset is byte-identical at the anchor and today, and all ten README hashes match. But `2e42d7f4` is where `docs/DESIGN-GUIDE.md:830` was discharged to the `python:*` spelling and where the report's own answer section reached its final text. An implementer branching from `7a774d57` reads a design guide whose worked TOML example still declares the bare names. Harmless in practice ("Environment setup" says from fresh main); worth one sentence, since the README hash table is already declared the tiebreaker.

*(Related nit, opposite direction: `DESIGN-GUIDE.md:830` now declares `python:compare-swap`, which the **shipped v4** loader refuses. Between now and P33's merge the guide's worked example is a config-load error. A-146 treats drift in either direction as drift; a one-line "as of P33" marker would settle it.)*

### R2-D4 — A-222's text still says "17 paths" *(minor)*

The manifest is now 19/92/16 and the handoff says 19. A-222's own sentence still reads *"The 17 paths in … `locked_carver_owned`"*. `decisions.md` is append-only and correctly so; A-223/A-224 do not restate the count. One clause in a future decision, or nothing — recorded so it is not rediscovered.

---

## (5) Corrected oracle / fixture matrix

### Requirement-to-oracle traceability, corrected

| requirement | oracle | status after round 2 | required repair |
|---|---|---|---|
| V5-1 hoist into `judgment.resolved` | O1 | **partial** — schema/requiredness correct and verified across all six rigor combinations; `base` provenance (declared vs resolved) unpinned by any fixture; `only-if` absent | add a symbolic-base fixture (A-143); add the forbidden-unless branch |
| V5-2 per-language closed operators | O2 | partial — artifact half now differential and sound; config half's own negative already passes on the pre-implementation tree for a different reason; work item 5's pinning test cannot distinguish the two refusal layers | name the observable (artifact emitted vs not); add a payload-only cross-language operator |
| V5-3 `equivalent` + all-inert terminal | O3 | **good, with a hole** — falsifiability genuinely achieved via `verify.py:976`; payload-presence and rigor-binding unconstrained in every layer | R2-B1(a)(b)(c) |
| V5-4 kill attribution | O4 | **broken** — 3 of 5 clauses have no discriminating fixture; the one locked test is satisfied by the schema alone; no producer for `kill_signal` | R2-B2; one fixture per clause |
| V5-5 helper provenance | O1/invariant 5 | partial — observable direction sound and differential; `executable-code` unruled; `statement-positions` unexercised | R2-B3 |
| 92-file migration | locked suite | **improved** — three negatives no migrated fixture can satisfy are now frozen | keep |
| gate stays green across the bump | O5 | **broken** — P25 harness (R2-D1); 18 oracles dropped (R2-D2) | both |
| producer emits v5 correctly | *none* | **uncovered** — every locked test validates a hand-written document | CA7 below |

### Pairwise axes

`declared_rigor` {R0 | R0,R1 | R0,R2 | R0,R1,R2 | R0,R3 | R0,R1,R3} × `judgment` {absent | +resolved+r1 | +resolved+r2 | +resolved+r3 | resolved-only} × `resolved.base` {absent | present} × `language` {python | go | sql | unregistered} × `judge.base` {absent | symbolic | full SHA} × `kill_attribution` {declared | unattributed} × `kill_signal` {absent | on killed | on survived | on equivalent} × `equivalence_artifact` {absent | declared} × `equivalent` {empty | non-empty | all-inert} × `helpers.role` {absent | mutation-sites | statement-positions | executable-code} × payload {present | deleted} × input `schema_version` {4 | 5} × producer {hand-written document | real `assay run`}.

### Combined-axis fixtures

**CA7 — a real `assay run` at R0,R1,R2 in a repo where `project_root != repo_top` (assay inside vbpub), asserting the emitted artifact.** The whole locked suite is document validation; this is the only shape that can witness a wrong producer. Assert: `resolved.source_roots` is the declared project-relative spelling (A-049/A-149), `resolved.base` equals the pre-snapshot resolution and R1's in-snapshot resolution (A-223f), and no `judgment.r1.base` survives. Round 1's CA6 in producer form — currently reduced to a string assertion over the carver's own templates.

**CA8 — `judge.base` declared as a branch name, `resolved.base` asserted equal to the 40-hex it resolves to.** A-143's required shape; nothing in P33 requires it today.

**CA9 — `INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT` with the `mutation` payload deleted, and the same code on an R3 claim.** Both must be refused. Both validate against the locked schema today.

**CA10 — `kill_attribution: "declared"` with (i) no `kill_signal_artifact`, (ii) a killed entry lacking `kill_signal`, and `unattributed` with (iii) a `kill_signal` on a *killed* entry.** The three clauses of invariant 4 the schema cannot express and no locked test exercises.

**CA11 — the post-v5 gate composite.** Install v5, then run the *whole* registered gate: P33's suite, the self-hosted lane, `qualify_topos.py`, and the independent witness. R2-D1 and R2-D2 are only visible here. This is round 1's CA5 lesson generalized: the first freeze was validated only against the pre-implementation tree; the second was validated against a post-implementation *verifier* but never against a post-implementation *gate*.

**CA12 — a lane declaring `judge.language = "sql"` with `sql:*` operators and R2.** Must load, then be refused by `_resolve_declared_adapters` with a complete artifact (A-139). Distinguishes the real per-language vocabulary from an implementation that hardcodes `python:*` at config load — both of which currently satisfy work item 5.

---

## (6) Verdict

**NOT READY.**

One requirement is mechanically unsatisfiable in scope (R2-D1: the P25 qualification harness compares a live v5 artifact against locked v4 templates inside the registered gate). One defect is unfixable *by the implementer at all* because it lives in a locked asset (R2-B1: the new terminal has no reason/payload/rigor conditional, so the forgery A-136 closed for its sibling is reopened). Two externally visible decisions remain unmade (R2-B2 `kill_signal` under `declared`; R2-B3 the third `helpers` role). One oracle-coverage loss is unacknowledged and large (R2-D2: 18 of 24 P26 tests). AUTHORING's criterion — *"Mark the handoff NOT READY if any externally visible decision, interface, example, bound, refusal, or proof source remains for the implementer to invent"* — is met.

**What is genuinely better, and should not be re-litigated in round 3.** The methodological fix worked. `refuses_only_the_defect` is the right shape and it does what it claims: I reproduced **17 failed / 2 passed** exactly, and confirmed the two passes are the `--check` contract and the pure-string source-roots assertion, both legitimately invariant. The differential construction cannot pass on a version mismatch. The manifest is now scanned over the git index and is materially complete — my independent scan found 138 matching tracked files, and the only 12 outside the buckets are P33's own assets and two prose reports. The scope↔manifest check is real (92/92). B1, B2, B7, B8, B9, B10, D1, D2, D4, D5 are all closed by re-derivation, not by assertion.

The residual defects share one root, and it is worth naming because it is the same root as round 1: **the carve was validated against the artifacts the reviewer named, not against the class the reviewer described.** Round 1 said "the gate runs a locked v4 suite"; the repair retired *that* suite and added a scanner pattern for *that* invocation, while a second gate step comparing against locked v4 templates went unexamined. Round 1 said "the score exclusion is unobservable"; the repair added a terminal and did not carry it to the three layers that constrain its three siblings. Round 1 said "`kill_attribution` has no declared source"; the repair derived it and did not ask what the derivation makes declarable. A defect class closes when its inventory closes, not when its instance does — which is A-141's rule, stated about audits, applying here to repairs.

---

## Appendix A — explicit pass/fail on each item the controller asked me to verify

| item | verdict | evidence |
|---|---|---|
| **D3 — P26 bytes untouched** | **PASS** | `git log -- carve-assets/P26/` returns `d610dbb4` ("JIT-freeze P26 attestation hardening") as the last touching commit; none of `7a774d57`/`2e42d7f4`/`147592e0` touches that tree. |
| **D3 — gate no longer invokes it against v5** | **PASS as an instruction, FAIL as a claim about the gate** | The gate *today* still runs P26's suite at `:229-231` (correct — work item 8 is the implementer's job, and `tools/tester-unified-gate.sh` is now in `scope.touch`). But the second half of the question — *does the gate still run anything that fails under v5* — is **yes**: `qualify_topos.py` at `:249` (R2-D1). The carver verified the instance, not the question. |
| **B1 — `--check` satisfiable post-work** | **PASS** | `migrate_v4_to_v5.py:32` reads `HERE / "verdict.schema.v4-snapshot.json"`, not the live path. The snapshot is **byte-identical** to the shipped v4 (`cmp` clean). `--check` exits 0 today and is invariant to work item 1, since neither input is the file being overwritten. |
| **B2 — SQL template re-derived independently** | **PASS** | Recomputed from the committed template and the shipped `judge_mutation`: buckets `killed=1 survived=1 equivalent=1 crashed=0 budget_exceeded=0`, `total=3`, `candidate_count=3` → not sentinel → `total≠0` → no crashed → **no budget_exceeded** → survived non-empty → `FAIL/MUTANTS_SURVIVED`, matching the recorded R2 claim; `rollup([PASS, FAIL])` → `FAIL`, exit 1, matching the top level. Arithmetic `1+1+1+0+0 == 3` holds. The round-1 contradiction is gone. |
| **B5/B6 — `ALL_MUTANTS_EQUIVALENT` distinct and observable** | **PASS on falsifiability, FAIL on completeness** | Falsifiable: `verify.py:976` re-derives R2 status via `judge_mutation`, so the two implementations differ observably — correct → `test_ca4_all_equivalent_is_inconclusive_not_pass` green; ignores `equivalent` → derives `PASS` against a recorded `INCONCLUSIVE` → red. The terminal is genuinely distinct from `NO_MUTANTS` (`total==0`, hence all buckets empty) and from `MUTATION_UNSUPPORTED` (payload-free early return); no overlap, no gap. **But** the terminal is unconstrained in schema and model (R2-B1), so the payload-free variant is not covered. |
| **Locked suite — 17/2, differential, no masking** | **PASS** | Reproduced verbatim: `17 failed, 2 passed`. The two passes are `test_migration_transform_still_verifies_after_the_work` and `test_ca6_source_roots_are_the_declared_spelling`, exactly as claimed. `refuses_only_the_defect` asserts the clean control returns `[]` in the same test, which cannot hold on a v4 verifier — the masking that defeated round 1 is structurally excluded. Two structural caveats, neither the same disease: `test_ca3`'s `len(failures) >= 2` is reachable from bucket arithmetic plus one clause (§2, O4), and the whole suite validates hand-written documents only (§2, cross-cutting). |
| **A-225 / B7 — reasoning checked against the real function** | **PASS** | `cli._resolve_declared_adapters` reads `for level in lane.rigor: if level != "R0": registry.get_adapter(built_in, lane.judge.language, level)`, and `_built_in_registry` registers Python only (`cli.py:20`). A Go or SQL R2 lane is refused **identically whether `go:*`/`sql:*` are populated or empty** — the withdrawal is correct, the transcription-grounds justification is sound, and work item 5's "add vocabulary, pin refusal unchanged" is the right shape. One residue: the pinning test as specified cannot distinguish the two refusal layers (§2, O2 attack A). |
| **Deferring the `judge.language` enum** | **PASS — sound conclusion, imprecise stated reason** | The conclusion is right but not for the reason given. Lanes declaring an unregistered language are *not* "currently valid": `judge.language` only exists on lanes declaring R1/R2/R3, and every such non-Python lane is already refused by `_resolve_declared_adapters`. The real argument is better: that refusal is A-139's *post-HEAD* refusal, which emits a complete artifact with one `ERROR/BAD_LANE_CONFIG` claim per declared level, whereas a load-time enum would refuse before output reservation and emit **no artifact at all** (A-181). Moving it earlier would lose evidence, not gain safety. Recommend restating on those grounds; deferral to P34 stands either way. |

## Appendix B — the three targets the carver flagged

**1. Does `ALL_MUTANTS_EQUIVALENT` interact correctly with `NO_MUTANTS` and `MUTATION_UNSUPPORTED`?**
The *ordering* is clean, and there is neither overlap nor a gap between the three: `MUTATION_UNSUPPORTED` returns before any payload arithmetic (no `Mutation` object exists); `NO_MUTANTS` requires `total == 0`, which under v5's extended arithmetic implies all five buckets empty, so it can never collide with a non-empty `equivalent`; the new branch requires `killed + survived == 0` with `equivalent` non-empty, which is unreachable whenever `survived` is non-empty — so "rank after `survived`" is in fact order-insensitive given the guard, and any placement after the `total == 0` test is equivalent. Worth recording, because the work item's "rank after survived" reads as load-bearing and is not.
**The defect is not in the interaction; it is that the new terminal inherited none of its siblings' constraints** — no payload conditional, no `rigor: R2` binding, no membership in `_MUTATION_ONLY_REASON_CODES` (R2-B1). And the fifth bucket was not propagated into the limit sentinel's own schema shape or the "four buckets" prose in two places.

**2. Does `judgment.allOf` express "base iff r1|r2" across every rigor combination?**
The **if** direction is correct and complete. Enumerated against the locked bytes and `JUDGE_FIELDS_BY_RIGOR`: `R0` (no judgment emitted — `_prepare_outcome` builds one only when a tier is non-None) ✓; `R0,R1` ✓; `R0,R2` ✓; `R0,R1,R2` ✓; `R0,R1,R3` ✓; `R0,R3` → `if` does not fire, `base` optional ✓ — and I confirmed the product genuinely cannot supply one, because the loader refuses `judge.base` on an R0,R3 lane as inert config (A-062), so this is a real absence rather than an omission. `judgment.required: ["resolved"]` closes the case where `resolved` itself is missing, and `allOf[0]` restores the at-least-one-tier guarantee.
The **only-if** direction does not exist (R2-B4): an r3-only judgment may carry `base` and validate. Unreachable by this producer, reachable by any foreign one, which is the population `verify.py` exists for.

**3. Does retiring P26's suite lose coverage the carry-forward doesn't reproduce?**
**Yes — 18 of 24 tests.** Only 4 are coupled to v4 artifact shape; the carry-forward reproduces one oracle (four attestation documents still validate, which I confirmed is satisfiable because all four templates are R0-only with no `judgment`). The lost 18 include A-212's process-group kill on a descendant-held pipe, aggregate bounds before the first Git call, literal-pathspec identity, annotated-tag peel refusal, atomic all-missing expiry, and deadline-remainder forwarding — behavioural boundaries v5 does not touch and which would keep passing unchanged. Full attribution table in R2-D2. Recommended repair: deselect the 4 template-coupled tests plus the gate-marker test and keep running the module, rather than retiring it.

---

*Reviewer note: this file was written, not committed. Git and merge mechanics belong to the controller under A-216. No repository file was modified by this review; probes ran under `/tmp` and read-only against the working tree.*
