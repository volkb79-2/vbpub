# Assay P33 pre-dispatch adversarial specification review — ROUND 4

> **Reviewer:** fresh Opus xhigh child forked from `CR-opus-0` (A-216), commissioned by the controller.
> **Review date:** 2026-08-11
> **Repository HEAD at review:** `8617051d0e10fbe2df4d556ada091dca57317f54` (clean tree, verified with `git rev-parse HEAD` / `git status --porcelain`)
> **Handoff:** `nyxloom-trove/handoffs/assay-P33-verdict-schema-v5.md`, read from disk
> **Declared `input_revision`:** `c22c607344bdf0e76c257d81cf6034e19e9c2aaa`
> **Assets:** `nyxloom-trove/carve-assets/P33/` — all **13** recorded sha256 digests recomputed: **13/13 OK**
> **Authoring doctrine:** `nyxloom/reference/AUTHORING.md`, revision `2026-08-08-r5`
> **Method:** the exact `Pre-dispatch adversarial handoff review` prompt, extracted from the live file with `awk` and whitespace-normalised `diff`ed against the controller's transcription — **identical, no transcription error**. All three prior reports were read *after* I had run the sweep, regressed it, and formed my own attack list. Every claimed fix was re-derived from primary sources; no probe modified a repository file (regressions ran against an isolated replica under `/tmp/sweeplab`).

## Result first

**NOT READY.**

The round-3 repairs are real and I confirm the largest of them by independent re-derivation. **All five sweep closure gaps are genuinely closed** (closure 147 → 189; `safeio.py`, `adapters/python.py`, `coverage_parsers/go_cover.py`, `release_wheel.py` and `test_distribution_gate.py` all verified individually). **The corrected decoy genuinely discriminates** — I regressed the sweep two independent ways and watched it fail both times, which is more than the carver claimed for it. The third `helpers` fixture is now a true differential and does exercise A-227's R1 branch. The P26 deselect list is right at four, and I re-read the A-210 test body and confirm it is shape-independent. The P25 siblings are exactly the declared projection. Digests, `--check`, frontmatter and L1 all pass.

Five findings block dispatch. The first is the one the carver named as its own weakest point, and the answer is worse than "theoretical gap":

1. **The `indirect-path-from-caller` heuristic misses four constructible mechanisms — and there is a live instance in this codebase today.** `tests/test_self_hosting.py` receives its artifact path from an environment variable, compares the artifact by whole-value equality, and is in the ownership manifest *only because the sweep reported it*. The sweep reports it **only** because it happens to contain the bare token `"python"`. The indirect heuristic matches it zero times.
2. **Three locked config tests call a `config` API that does not exist.** `load_lane`/`load` are absent; the real loader is `load_lane_file`. All three fail with `AttributeError`, will keep failing after a correct implementation, and the suite is uneditable.
3. **The locked suite's own docstring contradicts work item 8 on the deselect list**, carrying the pre-A-229 five-test list — reintroducing the exact security-oracle drop A-229 exists to prevent, in a file the implementer may not edit.
4. **Work item 8c (CA8) has no oracle**, so the A-143 gap raised in rounds 1, 2 and 3 can be silently skipped with the gate still green.
5. **The work items themselves create the sweep/manifest drift the manifest claims is impossible.**

Plus: three stale counts in work item 9 and one in work item 10; round 3's R3-D2 (site enumeration) silently dropped; `input_revision` wrong for the fourth consecutive round.

Machine shape is fine: frontmatter validates with zero errors, L1 passes, `depends_on` resolves.

---

## Priority one — the `indirect-path-from-caller` question, answered

The carver invited exactly this attack: *"it keys on argparse-ish markers, so a consumer that receives its path some other way would still be missed. The decoy pins the direct path; nothing yet pins the indirect one."*

### The heuristic, and what it can see

```python
INDIRECT = re.compile(r"(--manifest|argparse|sys\.argv|def main\()")
...
trees    = reads_frozen_tree(text)
indirect = bool(INDIRECT.search(text)) and not trees
if not trees and not indirect:
    continue
```

A file is reported when `COMPARISON` matches **and** (`trees` non-empty **or** `INDIRECT` matches). So a consumer is missed when it compares, its frozen path is not spelled literally, and it carries none of those four markers.

### Four constructible mechanisms, all missed

Evaluated against the shipped predicates directly:

| mechanism | `COMPARISON` | `trees` | `INDIRECT` | reported? |
|---|---|---|---|---|
| path from `os.environ[...]` | True | — | False | **False** |
| path from a parsed config file (`tomllib`) | True | — | False | **False** |
| class attribute assigned at import time | True | — | False | **False** |
| dict/mapping lookup keyed by scenario | True | — | False | **False** |

Each is an ordinary shape for a gate harness. None involves argparse, `sys.argv`, `--manifest`, or `def main(`.

### The live instance, which is what makes this blocking

`tests/test_self_hosting.py` is not hypothetical. It is executed by the registered gate (`tools/tester-unified-gate.sh:198`), and it is a genuine artifact consumer:

```
tests/test_self_hosting.py:72   ENV_VAR = "ASSAY_SELF_HOSTING_VERDICT"
tests/test_self_hosting.py:138  path = os.environ.get(ENV_VAR)
tests/test_self_hosting.py:155  assert document["schema_version"] == VERDICT_SCHEMA_VERSION
tests/test_self_hosting.py:167  assert document["claims"] == [ ... ]
```

Its artifact path arrives **through an environment variable** — the first row of the table above. Measured against the shipped sweep:

```
COMPARISON matches : True
INDIRECT  matches  : False          <-- the heuristic cannot see it
frozen trees (as shipped)            : ['nyxloom-trove/carve-assets', 'gate/python/release', 'gate/python/fixtures']
frozen trees (bare single-word tokens removed) : []
would it be reported at all?         : False
```

It is in the inventory **by accident**. `_seg_variants` emits every path component as a standalone quoted token:

```python
out += [f'"{p}"' for p in parts if p not in ("gate", "tests", "src")]
```

so `"gate/python/release"` contributes the bare token `"python"`, and any file containing that string is deemed to read a frozen tree. `test_self_hosting.py` contains `"python"` at line 81. That single token is the entire reason it appears in the sweep — and therefore the entire reason it appears in `migration-manifest.json`, where its recorded detection reason is literally `frozen-expectation-consumer(sweep)`.

**This is a masked default in AGENTS.md's precise sense** — a wrong predicate rendered harmless by an unrelated loose one, invisible to testing because every context you would observe it in runs the masking step. And the masking is load-bearing in the wrong direction: the obvious quality repair for the false-positive flood (17 of the 40 reported consumers are classified `direct` on a bare single-word token alone, including `test_config_rigor.py`, `test_cli_run.py`, `test_dependency_purity.py`) would silently drop a real consumer out of the inventory.

Worse, **the pin does not protect it.** `test_sweep_finds_every_known_consumer` freezes five paths; `tests/test_self_hosting.py` is not among them. So the locked oracle stays green through exactly the change that would lose it.

### Verdict on the controller's question

The mitigation is **not** sufficient, and the distinction the controller offered — theoretical gap versus what is actually reachable today — resolves against the carve. This is not a general heuristic with theoretical holes; it is a heuristic with a live miss in this repository, currently concealed by a predicate so loose that 42% of the inventory is noise. The handoff's own standard settles it: it justifies keeping `release_wheel.py` in the inventory because *"the closure claim has to be true regardless of whether a given instance happens to be harmless."* By that standard a consumer that is in the inventory only by token collision is not in the inventory.

`test_self_hosting.py` almost certainly survives v5 unchanged (it reads `VERDICT_SCHEMA_VERSION` rather than a literal, and its lane is R0-only so it emits no `judgment`). That is exactly the argument the handoff already rejects.

**Repair (carver-owned, mechanical):** drop the bare single-component variants from `_seg_variants` so `trees` means what it says; widen `INDIRECT` to the real mechanisms (`os.environ`, a parameter-typed path reaching a read+compare, a module-level constant assigned from a non-literal); and add `tests/test_self_hosting.py` to `test_sweep_finds_every_known_consumer`'s frozen set with a second decoy whose path arrives by environment variable, mirroring the dotted-import decoy that already works.

---

## (1) Blocking ambiguities

### R4-B1 — the inventory's indirect category, above

Recorded here as the first blocking finding; the full argument is in the priority-one section.

### R4-B2 — three locked config tests call a `config` API that does not exist

The three tests added this round to close R3-B3 all reach the loader like this:

```python
C.load_lane(toml, "demo") if hasattr(C, "load_lane") else C.load(toml)
```

Neither name exists. Probed against the shipped module:

```
has load_lane: False
has load     : False
public callables: find_lane_file, load_lane_file, parse_duration
```

The real entry point is `config.load_lane_file(path) -> LaneFile` (`config.py:443`). Run today, all three fail on the fallback:

```
test_config_refuses_a_cross_language_operator
  assert 'sql:drop-check' in "module 'assay.config' has no attribute 'load'"
test_config_accepts_a_matching_language_operator
  AttributeError: module 'assay.config' has no attribute 'load'
test_config_names_kill_signal_artifact_as_reserved_for_p34
  assert 'kill_signal_artifact' in "module 'assay.config' has no attribute 'load'"
```

They are red pre-implementation, which superficially reads as the intended controlled red — and it is the same masking that defeated round 1, one layer over. They are red for a reason unrelated to the behaviour they name, and **they stay red after a perfectly correct implementation**, because no work item creates `load_lane` or `load`.

The implementer's only route to green is to invent a public loader — its name (`load_lane` or `load`), its arity (`(path, lane_name)` or `(path)`), and its return value (`test_config_accepts_a_matching_language_operator` asserts `lane is not None`). `config.py` is in `scope.touch`, so nothing stops them; nothing specifies it either. That is AUTHORING's NOT READY trigger verbatim — *an interface remaining for the implementer to invent* — and the suite is locked under A-197, so they cannot instead fix the call.

This also falsifies the handoff's own claim in work item 10: *"Every negative in it is differential — it asserts a clean control verifies and the injected defect does not — so none of them can pass on a version mismatch."* These three are bare `pytest.raises` tests with the control in a *separate* test, and the separate control dies on the same `AttributeError`. The handoff's own Package-specific test emphasis predicts this failure precisely: *"A bare 'this is refused' passes on a pre-implementation tree for the wrong reason."*

**Repair:** rewrite the three calls against `load_lane_file`, and give each the `refuses_only_the_defect` shape the suite already mandates — a clean lane that loads and the same lane plus the one defect that does not, in one test.

### R4-B3 — the locked suite's docstring carries the pre-A-229 deselect list

`test_acceptance_v5.py`'s module docstring, lines 15-17:

> The gate now deselects exactly the 4 template-coupled tests plus `test_registered_gate_runs_locked_acceptance_from_the_wheel_and_marks_it`, and **keeps the other 19 running**.

That is 5 deselects / 19 kept — the list A-229 corrected. Work item 8 says four deselects and *"20 of P26's 24 tests keep running."* I verified all five named tests exist in P26's module, and I re-read the body of `test_all_structural_and_aggregate_bounds_precede_every_git_call`: it builds a fixture repo, monkeypatches `verify_exact_commit`, `is_ancestor`, `tree_entry_kind` and `path_is_current` to raise, and asserts `(Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT)`. Coupling check: `schema_version` False, `judgment` False, `verify_document` False, `template` False. The handoff is right and the locked asset is wrong.

No test asserts the count, so this is not mechanically unsatisfiable — it is worse in a different way. The implementer is told (context item 3) to read every asset, and A-197 tells them a locked carver-owned asset is authoritative and only the carver may correct it. An implementer resolving the conflict toward the locked file deselects five and drops A-210's aggregate-bounds-before-Git oracle — **the exact security oracle A-229(c) exists to restore, lost the same way it was lost the first time: two carver documents disagreeing and the wrong one being adopted.**

### R4-B4 — work item 8c has no oracle, so CA8 can be dropped silently

CA8 was raised in round 1 (B11), round 2 (O1/CA8) and round 3 (R3-D3). A-229(d) takes it, and work item 8c specifies it well: a scenario declaring `judge.base` as a tag on the base commit, asserting `judgment.resolved.base` equals the resolved 40-hex and differs from the declared string. I confirmed the machinery is real and in scope — `qualify_topos.py:404` `base_override`, `:424` `declared_base`, `:836` an existing caller.

But nothing checks it was done:

```
grep -in "base_override|CA8|8c|tag" test_acceptance_v5.py  ->  no match
```

The locked suite is document-validation only and cannot host a repository scenario, so a work item is the right vehicle — but the result is that the whole of A-143's requirement now rests on an unwitnessed instruction. An implementer who skips work item 8c leaves every oracle green, and the false-PASS that A-143 exists to catch (record `lane.judge.base`, the declared string) survives with the entire gate passing. This is A-147's shape exactly: *"work item 7 was not implemented, not declared skipped, and not mentioned in any commit body."*

The same gap applies to **work item 5**: R3-B3 asked for a frozen config-layer expectation covering the operator rename, the `go`/`sql` registry refusal, and work item 6. Three config tests were added (all broken, R4-B2) and the registry refusal was not among them —

```
grep -in "BAD_LANE_CONFIG|_resolve_declared_adapters|refuse_lane" test_acceptance_v5.py  ->  no match
```

so round 2's O2 attack A (a config-layer refusal and the adapter-registry refusal are indistinguishable, both `ERROR/BAD_LANE_CONFIG`, differing only in whether a complete artifact is emitted) is still live and still unnamed after three rounds.

**Repair:** either a locked assertion that the CA8 scenario exists (a grep-level structural test over `qualify_topos.py` is enough, and the suite already does structural checks of this kind in `test_third_consumer_is_in_the_migration_manifest`), or an explicit statement in work item 11's recorded-counts requirement that the CA8 scenario's result is part of the REPORT. And a locked fixture for work item 5 naming artifact-emitted-vs-not as the distinguishing observable.

### R4-B5 — the work items create the sweep/manifest drift the manifest declares impossible

The manifest's own `corrections_from_review` states:

> the sweep's own consumer list is now a detection reason, so the manifest and the inventory cannot drift apart — they are generated from the same run.

The union genuinely holds **today**: I ran the sweep at HEAD and diffed it against all three manifest buckets — **0 of 40 consumers absent**. That is a real PASS on the claim as stated.

But the claim is about a static JSON frozen at one commit and a live script, with nothing re-running the union. The controller asked me to construct a case where they still drift. The package's own instructions produce one:

- Work item 8 tells the implementer to *"add a second invocation of `carve-assets/P33/test_acceptance_v5.py`"* to `tools/tester-unified-gate.sh`.
- The sweep seeds from the gate script, so `test_acceptance_v5.py` enters the closure.
- Measured: `COMPARISON` True, `trees` `['nyxloom-trove/carve-assets', 'gate/python/release', 'gate/python/fixtures']` → **reported as a consumer**.
- Measured: `nyxloom-trove/carve-assets/P33/test_acceptance_v5.py` is in **no manifest bucket** — nor are `sweep_v4_consumers.py` or `migrate_v4_to_v5.py`.

So the moment the implementer performs work item 8 and then follows work item 8b — *"re-run it and confirm it reports no consumer you have not addressed"* — the sweep reports a consumer with no recorded ownership. That is `escalate_if` clause 3's trigger and R3-B2's defect, reintroduced by the handoff's own work items rather than by drift over time.

**Repair:** place P33's own assets in `locked_carver_owned` (they are locked carver assets), and add a locked test asserting *every* path the sweep reports is in some manifest bucket — which is the check that would actually make "cannot drift apart" true, and which would have failed here.

---

## (2) False-PASS attacks

**O1 — `judgment.resolved` representability and `base` provenance.**
*Attack (A-143, live for four rounds):* build `resolved.base` from `lane.judge.base`. Every locked template substitutes `@BASE_OID@` with a 40-hex value and `resolve_base` returns a full SHA unchanged, so declared and resolved are indistinguishable across all 38 tests. Work item 8c is the answer and has no oracle (R4-B4), so the attack survives an implementer who simply doesn't do it.

**O2 — closed per-language vocabulary.**
*Attack A:* implement the artifact-side prefix rule and skip the config side. The three tests that would catch this cannot run (R4-B2).
*Attack B:* implement `judgment.r2.operators` prefix-checking and not `mutant_outcome.operator`. A-148's existing check already ties payload operators to the declared set, so one rule covers for the other; no template carries a payload operator absent from the declared set. Unchanged from rounds 2 and 3.
*Attack C:* hardcode the vocabulary to `python:*` and reject `language = "sql"` at load. Work item 5 still names no observable distinguishing this from the adapter-registry refusal, and no locked fixture exists (R4-B4).

**O3 — equivalence pairing and the all-inert terminal.** I re-confirmed round 3's PASS. `verify.py:976` re-derives R2 status through `judge_mutation`, and the locked schema carries `ALL_MUTANTS_EQUIVALENT` in four places with the R2 binding and payload conditional. I found no surviving attack.

**O4 — attribution consistency.** CA10's three clauses have locked tests and the schema forbids `kill_signal` outside `killed`. Residue unchanged from round 3: `kill_attribution: declared` is unreachable from any real lane (work item 6 refuses the declaration), so the derivation is exercised only as hand-written documents, and a hardcoded `"unattributed"` passes every producer path this build has.

**O5 — the whole registered gate is green after v5.**
*Attack:* satisfy O5's letter. Repoint the two sites work item 8b names, deselect four, add the suite. The gate still dies at `qualify_topos.py`, because work item 8b names 2 of 6 real sites (R4-D2). And the P33 suite itself cannot go green, because three of its tests call a nonexistent API (R4-B2).

**Cross-cutting — the producer at v5.** Unchanged and correctly scoped: R2 has no producer witness anywhere in the registered gate, so the `equivalent` bucket, the extended arithmetic and `kill_attribution` are producer-tested only through implementer-migrated fixtures. CA7's deferral to P34 remains legitimate; round 3 agreed and I do too.

---

## (3) Missing implementation-packet content

1. **A sweep whose `indirect` category matches the mechanisms that actually occur**, a tightened `trees` predicate, and `tests/test_self_hosting.py` added to the frozen consumer set with an environment-variable decoy (R4-B1).
2. **The real `config` loader API in the three locked config tests**, and each rebuilt in `refuses_only_the_defect` form (R4-B2).
3. **A single authoritative deselect list.** The locked suite's docstring and work item 8 disagree (R4-B3).
4. **An oracle for work item 8c**, and a locked fixture for work item 5's registry refusal naming artifact-emitted-vs-not as the observable (R4-B4).
5. **Manifest buckets for P33's own assets**, and a locked test asserting sweep ⊆ manifest (R4-B5).
6. **Corrected counts** in work item 9 (92/19/16 → 110/21/17) and work item 10 (30 tests, 26/4 → 38 tests, 30/8) (R4-D1).
7. **The full site list for `qualify_topos.py`** — six sites, two named (R4-D2). Round 3 raised this and the answer section does not mention it.
8. **`helpers`' source and default rule** — carried unanswered from round 3's item 8. Work item 2 adds `Verdict.helpers`; nothing says where it comes from or what an empty default means, and A-213 is the ruling about exactly that boundary. P33 never populates it, so an empty default is honest — but the rule that made `evidence`/`declared_evidence` safe is still not restated.

---

## (4) Scope / dependency defects

### R4-D1 — four stale counts, in the two work items that carry completeness claims

| claim | handoff says | reality |
|---|---|---|
| `implementer_owned` | **92 files** (work item 9, and line 55) | **110** |
| `locked_carver_owned` | **19** (work item 9) | **21** |
| `carver_owned_prose_excluded` | **16** (work item 9) | **17** |
| locked suite pre-implementation | **30 tests, 26 failed / 4 passed** (work item 10) | **38 tests, 30 failed / 8 passed** |

All four verified directly (`json` over the manifest; a real run of the suite). The manifest grew when round 3 unioned the sweep's output into it; the handoff prose was never refreshed. `8617051d` edited the handoff for `input_revision` alone.

Work item 9 is the migration instruction and work item 10 is the acceptance baseline the implementer checks their tree against. An implementer who observes 38/30/8 against a stated 30/26/4 has no way to tell whether they have the wrong assets, and `escalate_if` does not cover a count mismatch. This is the same defect shape as A-103 and A-133, in the package that exists to close inventory gaps.

### R4-D2 — round 3's R3-D2 was not addressed and is not mentioned

Work item 8b still reads *"(`_EXPECTED_ROOT`, and the `normalized["judgment"]["r1"]["base"]` line, which v5 moves)"*. The real site list is unchanged:

| line | site | named? |
|---|---|---|
| `:51` | `_EXPECTED_ROOT` | yes |
| `:698` | `normalized.get("judgment", {}).get("r1", {}).get("base")` | **no** |
| `:715` | `normalized["judgment"]["r1"]["base"] = "@BASE_OID@"` | yes |
| `:928` | `_EXPECTED_ROOT / "missing-v4-template.json"` | **no** |
| `:962` | `template=_EXPECTED_ROOT / "missing-v4-template.json"` | **no** |
| `:1009` | `(primary, "pass-v4-template.json"), (missing, "missing-v4-template.json")` | **no** |

Because the v5 siblings are renamed, repointing `_EXPECTED_ROOT` alone yields `FileNotFoundError` at three sites. The "Answering round 3" section covers the sweep, the decoy, the deselect correction, CA8, work item 6, the third `helpers` role, the config negatives, the manifest union and `input_revision` — and is silent on this one. A-147 is the governing rule: a finding that will not be acted on is written down with its argument, never quietly dropped.

### R4-D3 — `input_revision` is wrong for the fourth consecutive round

`input_revision: c22c6073` is the commit that landed the round-3 asset repairs. But the hash table in `carve-assets/P33/README.md` — which the carve report designates as *"the anchor if the two ever disagree"* — was only refreshed one commit later, in `8617051d`:

```
digests listed at anchor c22c6073 : 13
digests listed at HEAD  8617051d  : 13
identical tables?                 : False
anchor digests matching real assets: 10 / 13
HEAD   digests matching real assets: 13 / 13
```

The three mismatches are exactly the assets round 3 changed (the manifest, the sweep, the acceptance suite). An implementer following the carve report's own verification instruction at the declared anchor finds three of thirteen assets failing their recorded digest and, per that instruction, concludes the assets are wrong.

The asset *bytes* are identical at both commits, and "Environment setup" says from fresh main, so this is recoverable — but the correct anchor is `8617051d`, and this is the fourth distinct wrong anchor across four rounds (`b03555d7` → `7a774d57` → `c22c6073`), which is now a pattern rather than an accident.

### R4-D4 — the manifest's own anchor is again one commit stale

`migration-manifest.json` records `frozen_at_main: 8877910b537eb319a5c96312846b012140c8adcf` — the **round-3 review commit**, not `c22c6073` and not HEAD. The manifest's own correction note says it was *"regenerated at the current anchor"*; it was regenerated at the commit immediately before the re-carve. This is R3-B2's exact shape, much milder (one commit rather than two re-carves), and the union check shows no practical divergence today — but the recorded provenance is wrong, and the round-3 finding it answers was specifically about a stale `frozen_at_main`.

### R4-D5 — the P26 API-compatibility constraint is still unnamed

Round 3 flagged, as related-and-unnamed, that four of the kept P26 tests call API inside P33's `scope.touch` (`Claim(...)`, `assemble_verdict`, `run_lane`, `refuse_lane`, `resolve_command_plan`, `load_lane_file`), so keeping P26's module converts uneditable locked tests into an API-compatibility contract across the migration. `grep -in "assemble_verdict|signature|API-compat"` over the handoff returns nothing. `escalate_if` clause 2 routes it correctly if it bites, but the constraint should be stated — especially now that R4-B2 pressures the implementer to *add* a public `config` function.

### R4-D6 — the decoy plants files in the real source tree *(minor, non-blocking)*

`test_sweep_finds_a_planted_decoy_consumer` writes `tests/_p33_sweep_decoy_entry.py` and `src/assay/adapters/_p33_sweep_decoy.py` into the worktree and unlinks them in a `finally`. Mitigations are real: the test asserts both paths are absent first, so a leak is caught loudly, and the `_`-prefixed entry file is not pytest-collectable. The gate runs no `-n auto` on this module, and work item 8 places the invocation after `assay run tester-unified` (`:185`), so there is no same-run interaction with A-175's post-command dirt guard. The residual hazard is cross-run: a leak from a killed process leaves the tree dirty, and the *next* gate run fails at `assay run` with `NO_MEASUREMENT/DIRTY_TREE` — a confusing first symptom for a cause three steps away. Worth one sentence in the work item.

---

## (5) Corrected oracle / fixture matrix

### Requirement-to-oracle traceability, corrected

| requirement | oracle | status after round 4 | required repair |
|---|---|---|---|
| V5-1 hoist into `judgment.resolved` | O1 | **partial** — schema complete and verified in both directions; `base` provenance now has a work item but no oracle | an oracle for work item 8c (R4-B4) |
| V5-2 per-language closed operators | O2 | **broken as proof** — artifact half sound; all three config-half oracles fail on a nonexistent API; registry refusal still unfrozen and its observable still unnamed | R4-B2; R4-B4 |
| V5-3 `equivalent` + all-inert terminal | O3 | **good** — falsifiable via `verify.py:976`; A-228 verified complete | none |
| V5-4 kill attribution | O4 | **partial** — CA10's clauses covered; work item 6's message oracle is correct in design but cannot run (R4-B2); `declared` unreachable from any lane | R4-B2 |
| V5-5 helper provenance | O1 / invariant 5 | **good** — third role is a true differential and the R1 branch is exercised | none |
| 92→110-file migration | locked suite | **counts stale** — work item 9 understates the bucket by 18 files | R4-D1 |
| gate stays green across the bump | O5 | **broken** — 4 unenumerated `qualify_topos` sites; locked suite cannot go green | R4-D2; R4-B2 |
| consumer inventory is closed | *escalate_if 3* | **broken** — live env-var consumer detected only by token collision; 17/40 false positives | R4-B1 |
| inventory and ownership cannot drift | manifest claim | **broken by the work items themselves** | R4-B5 |
| one deselect list | work item 8 | **broken** — locked asset contradicts the handoff | R4-B3 |

### Pairwise axes

`declared_rigor` {R0 | R0,R1 | R0,R2 | R0,R1,R2 | R0,R3 | R0,R1,R3} × `judgment` {absent | resolved+r1 | resolved+r2 | resolved+r3 | resolved-only} × `resolved.base` {absent | present | declared-symbolic | resolved-40hex} × `language` {python | go | sql} × `kill_attribution` {declared | unattributed} × `kill_signal` {absent | killed | non-killed} × `equivalence_artifact` {absent | declared} × `equivalent` {empty | non-empty | all-inert} × `helpers.role` {absent | mutation-sites | statement-positions | executable-code} × payload {present | deleted} × layer {schema | model | raw verifier} × **path-arrival** {literal | joined-components | argv | **environment variable** | **config value** | **caller parameter**} × consumer {P26 suite | qualify_topos | test_python_qualification | release_wheel | test_distribution_gate | **test_self_hosting** | **P33's own suite**}.

The last two axes are new this round and are where every surviving blocking finding lives.

### Combined-axis fixtures

**CA18 — the environment-variable decoy.** A second planted consumer whose frozen path arrives via `os.environ`, reached by the same real import edge the current decoy uses, asserted found. Today it is not found, which is the point. Pair it with `tests/test_self_hosting.py` added to `test_sweep_finds_every_known_consumer`'s frozen set, and with the bare-token variants removed from `_seg_variants` — so the pin fails if the accidental detection is ever cleaned up. This is CA14 with the axis round 4 added.

**CA19 — sweep ⊆ manifest, asserted.** A locked test that runs the sweep and requires every reported path to appear in some manifest bucket. It fails today for P33's own three assets, and it is the only construction that makes "cannot drift apart" a checked claim rather than a stated one.

**CA20 — the config-layer negatives, against the real API.** All three rebuilt on `load_lane_file`, each in `refuses_only_the_defect` form (clean lane loads, same lane plus one defect does not, one test). Plus the missing fourth: a lane with `language="sql"`, `sql:*` operators and R2, asserted to **load** and then be refused by `_resolve_declared_adapters` **with a complete artifact emitted** — the observable that separates the two `BAD_LANE_CONFIG` layers, unnamed since round 2.

**CA21 — CA8 with a witness.** Work item 8c's scenario, plus a locked structural assertion that the scenario exists (the suite already does this kind of check in `test_third_consumer_is_in_the_migration_manifest`). Without the witness, four rounds of raising A-143 end in an instruction that can be skipped for free.

**CA22 — the post-v5 gate composite, run for real.** Install v5, then run the whole registered gate: P33's suite, P26's module at 20 tests, the self-hosted lane, `qualify_topos.py` including the release smoke through `release_wheel.py`, and the independent witness. R4-D2's four unenumerated sites and R4-B2's API defect are both visible here in seconds. This is round 1's CA5, round 2's CA11 and round 3's CA17 restated a fourth time, because it has still never been run.

---

## (6) Verdict

**NOT READY.**

Five findings block. Two of them live in locked assets the implementer may not edit: three config tests call a `config` API that does not exist and cannot be made green without inventing a public interface (R4-B2), and the locked suite's docstring carries the pre-A-229 deselect list, pointing an implementer straight back at the security-oracle drop this round exists to have fixed (R4-B3). The inventory that is the package's central claim still misses a live consumer whose only reason for being in the inventory is a bare-token collision (R4-B1). The A-143 fixture raised in rounds 1, 2 and 3 is now a work item nothing witnesses (R4-B4). And the manifest's "cannot drift apart" claim breaks the moment the implementer performs work item 8 (R4-B5).

AUTHORING's criterion — *"NOT READY if any externally visible decision, interface, example, bound, refusal, or proof source remains for the implementer to invent"* — is met independently by R4-B2 (an interface) and R4-B4 (a proof source).

**What is genuinely closed, verified by re-derivation, and should not be re-litigated in round 5.**

- **All five sweep closure gaps.** Closure 147 → 189. `src/assay/safeio.py`, `src/assay/adapters/python.py`, `src/assay/coverage_parsers/go_cover.py`, `gate/distribution/release_wheel.py` and `tests/test_distribution_gate.py` are each in the closure; I checked them individually rather than trusting the count. `release_wheel.py` is correctly classified `indirect-path-from-caller`; `test_distribution_gate.py`'s `read_bytes() ==` idiom is now matched.
- **The corrected decoy discriminates, on both halves.** I built an isolated replica under `/tmp` and regressed the sweep twice. Reverting `_resolve_module` to v1 leaf-name resolution: decoy **not found**. Gutting `_seg_variants` to literal paths only: decoy **not found**. The carver's diagnosis of its own first decoy attempt was right, and the corrected version is a real oracle rather than a demand.
- **The P26 deselect list is correct at four.** All five candidate tests exist; I read `test_all_structural_and_aggregate_bounds_precede_every_git_call`'s body and confirm it touches no template, no `verify_document`, no `schema_version` and no `judgment`. The handoff's text is right — only the locked docstring is wrong.
- **Work item 6's observable genuinely discriminates.** The current closed-table message is `unknown judge.mutation key(s): kill_signal_artifact; expected only: jobs, max_mutants, operators` — it contains the field name but not `P34`, so the assertion on `"P34" in message` cannot pass on a wrong-reason refusal. The design of this oracle is correct; only its call site is broken (R4-B2).
- **The third `helpers` fixture is a true differential and exercises A-227's R1 branch.** R2 branch on `sql-r2`, R1 branch on `p25-pass` (coverage-bearing), and the negative built as `ca1-r3` clean versus the same document plus the one entry.
- **CA8's machinery is real** — `base_override` at `:404`, `declared_base` at `:424`, an existing caller at `:836` — so work item 8c is executable as written.
- **The sweep/manifest union holds at HEAD**: 0 of 40 reported consumers absent from the manifest.
- **P25's v5 siblings are exactly the declared projection**: `schema_version` 4→5 and `language`/`source_roots`/`base` moved `r1` → `resolved`, nothing else, verified by structural diff.
- **Mechanics:** 13/13 digests OK, `migrate_v4_to_v5.py --check` exits 0, `$id` is `urn:assay:schema:verdict:5`, frontmatter validates with zero errors, L1 passes, and my extraction of AUTHORING's review prompt is byte-identical to the controller's transcription.

**On the pattern, since this is the fourth round.** Rounds 1–3 each found the repair correct about the instance and stopped at the boundary of the instance. Round 4 is different in kind: the sweep's *closure* is now genuinely sound, and the decoy that pins it is genuinely falsifiable — that class really did close. What did not close is everything downstream of the repair. The three config tests written this round were never executed against the module they call. The deselect correction landed in the handoff and not in the locked asset that repeats it. The manifest was unioned with the sweep but not with the assets the work items add. The counts moved and the prose did not. Every one of these is a **verification** failure rather than a design failure, and all five are visible in under a minute from the project root: run the locked suite, run the sweep, diff the two counts, grep the two documents for the deselect list.

The productive move for round 5 is narrower than round 4's: fix the three `config` call sites, correct the docstring and the four counts, add `test_self_hosting.py` plus an env-var decoy to the sweep's frozen set, tighten `_seg_variants`, put P33's own assets in the manifest with a sweep ⊆ manifest assertion, give work item 8c a witness, and enumerate `qualify_topos.py`'s six sites. None is a design question. **And then run CA22 once** — the post-v5 gate composite that four review rounds have now asked for and that would have caught R4-B2 and R4-D2 without a reviewer.

---

*Reviewer note: this file was written, not committed. Git and merge mechanics belong to the controller under A-216. No repository file was modified by this review; the sweep regressions ran against an isolated replica under `/tmp/sweeplab`, and every other probe was read-only against the working tree.*
