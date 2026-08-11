# Assay P33 pre-dispatch adversarial specification review — ROUND 3

> **Reviewer:** fresh Opus xhigh child forked from `CR-opus-0` (A-216), commissioned by the controller.
> **Review date:** 2026-08-11
> **Repository HEAD at review:** `61de291243656656484d9901f0645f487949716d` (clean tree, verified with `git rev-parse HEAD` / `git status --porcelain`)
> **Handoff:** `nyxloom-trove/handoffs/assay-P33-verdict-schema-v5.md`, read from disk
> **Declared `input_revision`:** `7a774d57b41033e0f3de84cd5c2bb188f3cc401b`
> **Assets:** `nyxloom-trove/carve-assets/P33/` — **all 13 recorded sha256 digests recomputed with `sha256sum -c`: all OK**
> **Authoring doctrine:** `nyxloom/reference/AUTHORING.md`, revision `2026-08-08-r5`
> **Method:** the exact `Pre-dispatch adversarial handoff review` prompt, extracted from the live file and `diff`ed against the controller's transcription — identical apart from one trailing blank line in my extraction. Both prior reports were read *after* I had run the sweep and formed my own attack list, and every claimed fix was re-derived from primary sources.

## Result first

**NOT READY.**

The re-carve is again real progress, and I confirm by re-derivation: **A-228 is genuinely closed** (both forgeries now rejected, with a clean differential control), **the `iff` closure is complete across all sixteen rigor/base combinations**, **the two P25 v5 siblings are exactly the declared projection and nothing more**, and the deselect count is right (5 named, all exist, 19 kept). The suite reproduces **26 failed / 4 passed** exactly, and the four passes are legitimately implementation-invariant.

Five findings block dispatch. The first is the one the carver asked me to attack first, and it is the same disease for the third consecutive round:

1. **The sweep's closure claim is false, and I can demonstrate a live consumer it misses on two independent grounds.** `gate/distribution/release_wheel.py` executes inside the registered gate and compares against a carver-frozen manifest; it is absent from the sweep's closure *and* invisible to its component list. Four further provable gaps below.
2. **`tests/test_python_qualification.py` — the sweep's own headline find — is absent from `migration-manifest.json` entirely**, because the manifest was frozen at `b22ebd56` (the *round-1* review commit) and never regenerated. Work item 9 tells the implementer to work the manifest.
3. **The locked suite contains zero config-layer tests**, though O2's own observable is "refused at config load" and three work items change `config.py`.
4. **Work item 6's config refusal is unfalsifiable as written** — `judge.mutation` is already a closed table and already refuses `kill_signal_artifact` today.
5. **The `executable-code` test misuses the suite's own differential helper** and never exercises the branch A-227 added it to witness.

Plus: the deselect list is over-inclusive by one, dropping a non-v4-coupled security oracle; `input_revision` is stale for the third round running; and CA8's deferral is not legitimate — the machinery is already in scope.

Machine shape is fine: frontmatter validates, L1 passes, `depends_on` resolves.

---

## (1) Blocking ambiguities

### R3-B1 — the sweep does not close the class it was built to close

This is priority one, so it is answered in full. I read the logic rather than trusting the output, then probed each mechanism against the live tree.

**The closure argument has three independent holes, all proven, not inferred.**

**(a) Subpackage modules are structurally unreachable.** `local_imports` resolves an import to `path.parent/<leaf>.py` or `ROOT/src/assay/<leaf>.py`, where `leaf = name.split(".")[-1]`. So `from assay.adapters.python import PythonAdapter` resolves `leaf="python"` against `src/assay/python.py`, which does not exist. Measured against the real closure:

```
src/assay/**.py on disk: 30    MISSING from the 147-file closure: 12
   src/assay/adapters/{__init__,base,go,python}.py
   src/assay/coverage_parsers/{__init__,cobertura,coverage_py_json,go_cover,lcov,model}.py
   src/assay/safeio.py, src/assay/__init__.py
```

`src/assay/adapters/**` and `src/assay/coverage_parsers/**` are *entirely* outside the sweep's reach. `adapters/python.py` is in P33's own `scope.touch`.

**(b) `from . import X` is invisible.** `elif isinstance(node, ast.ImportFrom) and node.module` — a plain relative `from . import safeio` has `module=None` and is skipped. That form appears nine times in `src/assay/` (`cli.py:54`, `runner.py:84`, `attestation.py:72`, `coverage.py:58`, `canary.py:88`, `isolation.py:48`, `measurability.py:26`, `mutation.py:113`, `cli.py:53`). Proven consequence: `src/assay/safeio.py` falls out of the closure despite being imported by `runner`, `coverage` and `attestation`.

**(c) Subprocess invocation is not followed — and this one is live.**
`gate/python/qualify_topos.py:539-541` builds a helper path from `/`-joined parts and executes it inside the registered gate:

```python
helper = source_repo / "assay" / "gate" / "distribution" / "release_wheel.py"
verified = _run([str(venv / "bin" / "python"), str(helper), "verify",
                 "--wheel", str(wheel), "--manifest", str(manifest)])
```

where `manifest = RELEASE_ROOT / "release-manifest.json"` → `gate/python/release/P25/release-manifest.json`, a carver-frozen closed four-field document (`schema_version`, `filename`, `version`, `sha256`, A-200). `release_wheel.py verify` re-derives those facts and compares them against that frozen expectation. **This is a consumer of a frozen expected artifact inside the registered gate's transitive execution path — exactly the sweep's stated subject — and the sweep misses it twice:**

```
gate/distribution/release_wheel.py: in_closure=False
                                    dirs=[]   signals=['schema_version_literal','document_equality']
gate/python/release/P25/  components: gate, python, release, P25
   matches ("carve-assets","expected","fixtures","verdicts"): False
```

It is not in the closure (subprocess, not import), and even if it were, `frozen_dir_hits` returns empty because its directory name is in none of the four hardcoded components. It is not a *verdict-v4* consumer, so P33 is not broken by it — but the claim under test is closure, and closure fails.

**(d) The signal set misses a live comparison idiom.** `tests/test_distribution_gate.py` **is** in the closure, **does** hit the `carve-assets` component, and compares live bytes against locked P24 carve assets:

```python
assert (DISTRIBUTION / "build-requirements.txt").read_bytes() == (
    LOCKED_ASSETS / "build-requirements.txt").read_bytes()
```

The sweep reports `signals=[]` for it and does not flag it, because `X.read_bytes() == Y.read_bytes()` matches none of the five regexes. `assertEqual`, `filecmp.cmp`, `json.load(a) == json.load(b)` and `difflib` are equally invisible.

**(e) The seed extractor does not know what the gate actually runs.** The gate's primary Python invocation is `assay run tester-unified` (`tools/tester-unified-gate.sh:185`), whose argv lives in **`assay.toml`** — `["python","-m","pytest","tests",...]` — and the sweep never reads `assay.toml`. `tests/` enters the closure only because two *other* lines happen to spell `-m pytest tests`: the failure-only diagnostic rerun at `:187` and the independent witness at `:198`. The third consumer, `tests/test_python_qualification.py`, is reachable **only** through that incidental seed. The sweep also never scans the gate script's own three inline `python - <<PYEOF` heredocs for consumption signals.

**The concretely constructible fourth consumer the controller asked for.** Two, one of them real:
- *Real, today:* `gate/distribution/release_wheel.py` (above).
- *Constructible:* any consumer placed in `src/assay/adapters/` or `src/assay/coverage_parsers/` — for instance P34's `adapters/sql.py` loading a frozen operator manifest from `carve-assets/P34/expected/`. It would be gate-reachable through the registry, would name `carve-assets` and `expected`, would compare documents — and the sweep would never open the file, because no seed can reach that directory.

**Why this blocks.** A-226's entire justification is *"the correct closure is an inventory of consumers, not another content pattern."* The inventory is the load-bearing claim, `escalate_if` clause 3 delegates to it, and work item 8b instructs the implementer to "re-run it and confirm it reports no consumer you have not addressed." A tool with five demonstrated blind spots cannot carry that weight, and **nothing in the locked suite runs or pins it** (`grep -c sweep test_acceptance_v5.py` → 0), so its output is implementer-self-graded against an unfrozen list.

**Repair (carver-owned, cheap):** resolve dotted module paths through the real package layout rather than a leaf name; handle `ImportFrom` with `module=None`; follow subprocess targets (the same `/`-joined-components logic already used for frozen dirs, applied to *executable* targets); derive the frozen-expectation predicate from "reads a path under a carver-owned locked tree, by any idiom" rather than four directory names and five regexes; seed from `assay.toml`'s declared argv as well as the gate script; and freeze the sweep's expected output as a locked oracle so a future consumer changes it.

### R3-B2 — the third consumer is absent from the ownership manifest

`migration-manifest.json` records `frozen_at_main: b22ebd56c4af3f89cd393ef013ca292f722b16ee` — *the round-1 review commit*, two re-carves ago. Bucket membership, checked directly:

| path | manifest bucket |
|---|---|
| `gate/python/qualify_topos.py` | `implementer_owned` |
| `tools/tester-unified-gate.sh` | `implementer_owned` |
| `tests/test_verdict_schema_is_packaged.py` | `implementer_owned` |
| **`tests/test_python_qualification.py`** | **absent from all three buckets** |

The scan patterns are content-based (`'"schema_version": 4'`, `source_roots` near `judgment`/`r1`, a bare operator name, the v4 urn, a P26 invocation). `tests/test_python_qualification.py` reads `pass-v4-template.json` — a *filename*, matching no pattern. That is precisely the class the sweep was built to catch, and the sweep's finding was never folded back into the manifest.

The handoff calls the manifest "the ownership boundary" (context item 3) and work item 9 says "migrate every path in `migration-manifest.json`'s `implementer_owned` bucket — 92 files." Work item 8b separately names the file, so an implementer reading both instructions is covered — but the two disagree about the file's status, and the mechanical artifact the package points at as authoritative omits the package's own headline finding. `escalate_if` clause 3 will not fire, because a work item *does* address it. This is A-141's rule failing one layer up: the inventory closed in prose and in a work item, not in the inventory.

### R3-B3 — the locked suite has no config-layer test, and O2's observable is a config-layer refusal

`grep -c "load_lane_file\|LaneConfigError" nyxloom-trove/carve-assets/P33/test_acceptance_v5.py` → **0**. Every one of the 30 locked tests validates a hand-written document through `verify_document`.

O2's observable is: *"a lane whose resolved language is python and which declares a `sql:` or `go:` operator is **refused at config load**."* Work item 4 renames the vocabulary in `config.py`'s validation; work item 5 requires a test pinning `_resolve_declared_adapters`' refusal for both `go` and `sql`; work item 6 requires a new config refusal. **All three are delegated to implementer-authored tests**, in the package whose stated dominant failure mode is that implementer-authored tests share the implementation's interpretation, and after two rounds in which the carver's own answer was to freeze the negatives rather than delegate them.

Round 2 flagged the narrow version of this (work item 5's pinning test cannot distinguish a config-load refusal from an adapter-registry refusal, since both surface as `ERROR/BAD_LANE_CONFIG` and differ only in whether a complete artifact is emitted). That distinction is still unnamed, and the broader gap — no frozen config oracle at all — is new to this round.

### R3-B4 — work item 6's refusal already exists, so no test can distinguish implementing it from not

`judge.mutation` is already a **closed** table. Probed against the live loader:

```
baseline (no new key)   : ACCEPTED
kill_signal_artifact    : unknown judge.mutation key(s): kill_signal_artifact; expected only: jobs, max_mutants, operators
equivalence_artifact    : unknown judge.mutation key(s): equivalence_artifact; ...
totally_made_up         : unknown judge.mutation key(s): totally_made_up; ...
```

Work item 6 says *"`config` must refuse `judge.mutation.kill_signal_artifact` at load with a typed error naming P34."* Two readings, with different externally visible behaviour and no oracle to choose between them:

- **(i) Nothing to do.** The closed table already refuses it. Then the work item asks for no change, and A-060's rule bites: an implementation that does nothing and one that "implements the refusal" are indistinguishable.
- **(ii) Add the key to the known set specifically to reject it**, with a bespoke message naming P34. That requires inventing the message text and the error type, and it makes `MutationConfig` carry a field whose only purpose is refusal.

The handoff does not say which, no locked test covers either, and the reason code is unnamed (`LaneConfigError`/`BAD_LANE_CONFIG` is the obvious fit but is not stated). A-227's reasoning — *"making a field declarable while shipping no producer … creates a legal lane whose own artifact has no representable shape"* — argues for (i), i.e. *leave the closed table alone*; the work item's imperative reads as (ii).

Related asymmetry left open: `equivalence_artifact` is in exactly the same position (refused today by the same closed table), so `judgment.r2.equivalence_artifact` is equally unreachable from a real lane. A-227 rules only on `kill_signal_artifact`. The defensible reason is that the pairing rule tolerates a declared artifact with an empty bucket — but that reason is nowhere stated, so the implementer must infer the asymmetry.

### R3-B5 — the `executable-code` test defeats the suite's own differential discipline and never exercises the branch it exists for

`test_helpers_role_executable_code_has_a_defined_correspondence` (added this round to answer R2-B3) does:

```python
clean  = load(HERE / "expected" / "sql-r2-v5-template.json")        # R0,R2
...
broken = load(HERE / "expected" / "ca1-r3-no-base-v5-template.json") # R0,R3  <-- different document
broken["helpers"] = [{"role": "executable-code", ...}]
refuses_only_the_defect(verify_document, clean, broken, ...)
```

`refuses_only_the_defect` asserts `verify(clean) == []` and `verify(broken) != []`. Its whole purpose — stated in its own docstring — is that the control and the broken document are **the same document modulo one injected change**, so the rejection is attributable to the defect. Here they are two different templates, so the attribution is lost: CA1-plus-helpers could be rejected for any reason and the SQL control would still verify clean. The sibling test `test_helpers_entry_requires_a_correspondingly_judged_claim` does it correctly, loading `ca1` twice.

Worse, A-227 rules `executable-code` ⇒ **an R1-with-`coverage` OR an R2-with-`mutation` claim**, and the test's own docstring repeats it — but only the R2 branch is exercised. **The R1 acceptance branch has no fixture.** A wrong implementation that treats all three roles identically to `mutation-sites` ("any helpers entry requires an R2 claim with `mutation`") passes both assertions, and is wrong exactly where A-227 differs from the naive rule. `p25-pass-v5-template.json` is R0,R1-with-coverage and is the obvious missing fixture; `statement-positions` is likewise never exercised in any template.

This is round 2's own O4 finding — *"a vocabulary needs a rejected value per language, not one rejection standing for all three"* — recurring in the test written to close it.

---

## (2) False-PASS attacks

**O1 — `judgment.resolved` representability.**
*Attack (A-143, now unfixed for three rounds):* build `resolved.base` from `lane.judge.base` — the declared string — rather than the resolved commit. Every locked template substitutes `@BASE_OID@` with a 40-hex value, and `resolve_base` on a full SHA returns that same SHA, so declared and resolved are indistinguishable across the entire suite. The carve report acknowledges this and defers it (CA8). See R3-D3 for why the deferral does not hold.

**O2 — closed per-language vocabulary.**
*Attack A:* implement the artifact-side prefix rule and skip the config side entirely — no locked test touches config (R3-B3), so only implementer-authored tests would notice.
*Attack B:* implement `judgment.r2.operators` prefix-checking and not `mutant_outcome.operator`. A-148's pre-existing check already ties payload operators to the declared set, so one rule covers for the other; no template carries a payload operator absent from the declared set.
*Attack C:* hardcode the vocabulary to `python:*` and reject `language = "sql"` at load. Work item 5's pinning test cannot distinguish that from the adapter-registry refusal, because both render `ERROR/BAD_LANE_CONFIG` and the work item does not name the distinguishing observable (artifact emitted vs. not).

**O3 — equivalence pairing and the all-inert terminal.** Genuinely hardened this round. `verify.py:976` re-derives R2 status through `judge_mutation`, so ignoring `equivalent` renders `PASS` against a recorded `INCONCLUSIVE` and fails `test_ca4_*`; and A-228's schema branch now rejects both forgeries (reproduced below). I found no surviving attack on this oracle.

**O4 — attribution consistency.** Improved: CA10's three clauses now have tests. Residue: the `declared` half can only ever be exercised as a hand-written document, because no lane can declare `kill_signal_artifact` (R3-B4), so the derivation `kill_attribution = declared` is dead code in every real run and no test can distinguish a correct derivation from a hardcoded `"unattributed"`.

**O5 — the whole registered gate is green after v5.**
*Attack:* satisfy O5's letter. Repoint `_EXPECTED_ROOT` and the one `normalized["judgment"]["r1"]["base"]` line named in work item 8b, deselect five, add the suite. The gate still dies, because `qualify_topos.py` reads the v4 filenames at three further sites the work item does not enumerate (`:928`, `:962`, `:1009`) and checks `judgment.r1.base` at a second, differently-spelled site (`:698`, a `.get()` chain). See R3-D2.

**Cross-cutting — the producer at v5.** Round 2's observation stands and the carver's answer is only partly right. Once repointed, `qualify_topos.py` *is* a real producer-side v5 check at R0,R1 — so the carve report's claim that "the only v5 producer path that exercises `judgment.resolved` end-to-end is a real R1/R2 lane — which for a *new* language is P34's" understates what P33 already has. What genuinely has no producer witness is **R2**: no real R2 lane runs anywhere in the registered gate, so the `equivalent` bucket, the extended arithmetic and `kill_attribution` are producer-tested only through implementer-migrated fixtures in `tests/**` — the common-mode axis this package exists to defend.

---

## (3) Missing implementation-packet content

1. **A sweep whose closure is sound**, and a locked oracle pinning its output (R3-B1).
2. **`tests/test_python_qualification.py`'s manifest bucket**, and a manifest regenerated at the current anchor (R3-B2).
3. **Any frozen config-layer expectation** — for the operator rename, the `go`/`sql` registry refusal, and work item 6 (R3-B3).
4. **Which reading of work item 6 is intended**, and if (ii), the exact error type, reason code and message (R3-B4). Plus `equivalence_artifact`'s config status.
5. **A fixture for `executable-code` on an R1-with-`coverage` lane**, and one for `statement-positions` at all; and a corrected differential construction for the existing test (R3-B5).
6. **The full site list for `qualify_topos.py`** — five sites, two named (R3-D2).
7. **A fixture where declared and resolved `base` genuinely differ** (CA8) — see R3-D3 for why this is not legitimately deferrable.
8. **`helpers`' source and default rule.** Work item 2 adds `Verdict.helpers`; nothing says where it comes from or what an empty default means. A-213 ruled that an unchecked empty tuple at an assembly boundary is a shadowing default and must be bound to the lane's own source. P33 never populates `helpers`, so an empty default is honest here — but the rule that made `evidence`/`declared_evidence` safe is not restated, and `assemble_verdict` is the boundary A-213 was written about.

---

## (4) Scope / dependency defects

### R3-D1 — the deselect list is over-inclusive by one, dropping a live security oracle

The count is right: all five named tests exist, 24 − 5 = 19 kept. But I attributed each test by reading its body, and **`test_all_structural_and_aggregate_bounds_precede_every_git_call` is not v4-coupled.** It calls `attestation.load_attested_evidence` through the module's `_load` helper, monkeypatches four `git` functions to explode, and asserts `(Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT)`. It touches no template, no `verify_document`, no `schema_version`, no `judgment`. `src/assay/attestation.py` is not in P33's `scope.touch`, and both enum members survive v5 unchanged, so the test would keep passing.

This is A-210's *"all structural and aggregate bounds precede every Git call"* oracle — a bound-before-work boundary with a witnessed incident behind it. Round 2's own report is internally inconsistent about it: its R2-D2 table lists it among the "4 genuinely v4-coupled", while its Appendix B lists *"aggregate bounds before the first Git call"* among the 18 independent oracles worth keeping. A-226 adopted the table. Deselecting exactly four — the three template-coupled tests plus the gate-marker test — keeps 20 and loses nothing.

*Related, unnamed:* 4 of the 19 kept tests call API inside P33's `scope.touch` — `test_runner_binds_evidence_batch_to_lane_source_before_any_work` pins `Claim(...)`, `assemble_verdict`, `run_lane`, `refuse_lane` and `resolve_command_plan`; three others pin `load_lane_file`. Keeping P26's module therefore converts 19 uneditable locked tests into an API-compatibility contract across the migration. `assemble_verdict` is keyword-only with defaults, so adding `helpers=()` is compatible — but nothing in the handoff says the constraint exists, and A-197 means an implementer who needs to change a signature is BLOCKED. `escalate_if` clause 2 routes it correctly; it should still be named.

### R3-D2 — work item 8b enumerates two of at least five sites in `qualify_topos.py`

Work item 8b names *"(`_EXPECTED_ROOT`, and the `normalized["judgment"]["r1"]["base"]` line, which v5 moves)"*. The real site list:

| line | site | named? |
|---|---|---|
| `:51` | `_EXPECTED_ROOT` | yes |
| `:698` | `normalized.get("judgment", {}).get("r1", {}).get("base") != base_oid` | **no** — different spelling, same fact |
| `:715` | `normalized["judgment"]["r1"]["base"] = "@BASE_OID@"` | yes |
| `:928` | `_EXPECTED_ROOT / "missing-v4-template.json"` | **no** |
| `:962` | `template=_EXPECTED_ROOT / "missing-v4-template.json"` | **no** |
| `:1009` | `((primary, "pass-v4-template.json"), (missing, "missing-v4-template.json"))` | **no** |

Because the v5 siblings are renamed (`p25-pass-v5-template.json`, not `pass-v4-template.json`), repointing `_EXPECTED_ROOT` alone yields `FileNotFoundError` at three sites. Work item 8b does name the target filenames, so an attentive implementer recovers — but the parenthetical reads as a complete enumeration and is not. A-149's standing rule after the second copy-based path defect was *"the rule is now stated as 'enumerate', not 'check the obvious one'."*

I checked the one semantic dependency that could have been worse and it survives: `_wrong_source_root`'s decoy at `:898-935` requires `"judgment"` to be among the differing top-level keys, and since v5 moves `source_roots` from `judgment.r1` to `judgment.resolved` — still under `judgment` — the assertion still discriminates. Nobody had checked that; it holds by luck rather than design.

### R3-D3 — CA8's deferral does not hold: the machinery is already in scope

The carve report defers the declared-vs-resolved `base` fixture on the grounds that *"it needs a fixture whose lane declares a branch name — which requires a real repository, so it belongs with CA7 in the producer-side proof rather than in a document-validation suite."*

The real repository already exists, already writes the lane, and is already parameterized for a symbolic base, in a file P33 already edits:

- `qualify_topos.py:381` writes `base = {json.dumps(base)}` into the disposable lane's `assay.toml`;
- `:404` `base_override: str | None = None` is a materialization parameter;
- `:424` `declared_base = base_override if base_override is not None else base_oid`;
- `:836` already passes `base_override=tag` for the `base-is-head` scenario.

`gate/python/qualify_topos.py` is `implementer_owned`, in `scope.touch`, and work item 8b already edits it. A CA8 scenario is a tag on the *base* commit rather than HEAD, plus one assertion that `judgment.resolved.base` equals the 40-hex it resolves to and not the declared string. P33 is the package that collapses two independently-resolved base values into one field; A-143's ruling is explicit that *"the fixture must make resolved and declared genuinely different, or it proves nothing"*; round 1 raised it (B11), round 2 raised it (O1/CA8), and it is now deferred a third time on a premise that does not survive checking.

**CA7, by contrast, is a legitimate boundary** — with an inaccurate stated reason. Real R2 producer coverage genuinely does need an R2 lane the gate does not have, and carving that inside P33 would mean carving P34's proof. But the report's claim that P33 has no producer-side v5 check understates it: once repointed, `qualify_topos.py` exercises the R0,R1 producer end-to-end against a frozen v5 expectation. The honest statement is "R1 producer coverage exists via P25's harness; R2 producer coverage is document-only and belongs to P34."

### R3-D4 — `input_revision` is stale, for the third round *(non-blocking, but recurring)*

`input_revision: 7a774d57` predates this round's assets:

```
sweep_v4_consumers.py            : ABSENT at the declared input_revision
expected/p25-pass-v5-template.json   : ABSENT
expected/p25-missing-v5-template.json: ABSENT
test_acceptance_v5.py            : present but CHANGED (a103a1c3 -> 80d5f031)
verdict.schema.v5.json           : present but CHANGED (de180687 -> 0e894676)
```

The correct anchor is `fba0b88d`. At `7a774d57` the schema is the one round 2 declared defective (no `ALL_MUTANTS_EQUIVALENT` branch, no `only-if`), the suite is 19 tests / 17 failed / 2 passed rather than the 30 / 26 / 4 work item 10 asserts, and work item 8b's target templates do not exist. Mitigations are real — "Environment setup" says from fresh main, and the README hash table is the declared tiebreaker and **all 13 digests verify OK** — but AUTHORING Level 2 is explicit that the daemon parses frontmatter *without reading the body*, so the machine-readable anchor points at the superseded carve. Round 1 raised this as D4; it is now a third distinct anchor alongside the manifest's `frozen_at_main: b22ebd56`. Three anchors, three different commits, in one package.

---

## (5) Corrected oracle / fixture matrix

### Requirement-to-oracle traceability, corrected

| requirement | oracle | status after round 3 | required repair |
|---|---|---|---|
| V5-1 hoist into `judgment.resolved` | O1 | **partial** — schema correct and now complete in both directions (16/16 verified); `base` provenance still unpinned by any fixture | CA8 inside the P25 harness (R3-D3) |
| V5-2 per-language closed operators | O2 | **broken as proof** — artifact half sound; config half has no frozen oracle at all; the two refusal layers remain indistinguishable | R3-B3; name the observable |
| V5-3 `equivalent` + all-inert terminal | O3 | **good** — falsifiable via `verify.py:976`, and A-228's constraints verified rejecting both forgeries | none |
| V5-4 kill attribution | O4 | **partial** — CA10's clauses now covered; `declared` unreachable from any lane, so the derivation is untestable | R3-B4 |
| V5-5 helper provenance | O1 / invariant 5 | **broken** — the third role's test is non-differential and misses A-227's R1 branch; `statement-positions` unexercised; invariant 5 in the packet omits `executable-code` | R3-B5 |
| 92-file migration | locked suite | good — three negatives no migrated fixture can satisfy are frozen | keep |
| gate stays green across the bump | O5 | **broken** — 3 unenumerated v4 filename sites + 1 unenumerated base site (R3-D2); one over-deselection (R3-D1) | both |
| consumer inventory is closed | *escalate_if 3* | **broken** — five demonstrated blind spots; one live missed consumer; no oracle | R3-B1 |
| ownership boundary is complete | work item 9 | **broken** — the round-3 consumer is not in the manifest | R3-B2 |

### Pairwise axes

`declared_rigor` {R0 | R0,R1 | R0,R2 | R0,R1,R2 | R0,R3 | R0,R1,R3} × `judgment` {absent | resolved+r1 | resolved+r2 | resolved+r3 | resolved-only} × `resolved.base` {absent | present} × `language` {python | go | sql} × `judge.base` {absent | symbolic-to-base | symbolic-to-HEAD | full SHA} × `kill_attribution` {declared | unattributed} × `kill_signal` {absent | killed | non-killed bucket} × `equivalence_artifact` {absent | declared} × `equivalent` {empty | non-empty | all-inert} × `helpers.role` {absent | mutation-sites | statement-positions | executable-code} × payload {present | deleted} × layer {schema | model | raw verifier} × producer {hand-written | real `assay run`} × consumer {P26 suite | qualify_topos | test_python_qualification | release_wheel}.

### Combined-axis fixtures

**CA13 — `executable-code` on an R0,R1-with-`coverage` lane, asserted ACCEPTED**, built from `p25-pass-v5-template.json`; plus `statement-positions` on the same document, and `statement-positions` on an R2-only document asserted REFUSED. Closes R3-B5 and gives A-227's "either" its missing half. The existing third-role test must also be rebuilt so `clean` and `broken` are the same document.

**CA14 — the sweep's own output, frozen.** Run `sweep_v4_consumers.py` and assert the exact consumer set, plus a planted decoy: a temporary file under `src/assay/adapters/` that reads a `carve-assets/**/expected/` document and compares it. The sweep must report it. Today it cannot, which is the point.

**CA15 — CA8 inside the P25 harness.** A scenario declaring `judge.base` as a tag on the base commit (not HEAD, which `_check_base_is_head` already occupies), R0,R1, asserting `judgment.resolved.base` equals the resolved 40-hex and differs from the declared string. Uses `base_override`, which already exists.

**CA16 — the config-layer negatives, frozen.** A lane with `language="python"` and a `sql:`/`go:` operator → refused at load; a lane with `language="sql"`, `sql:*` operators and R2 → loads, then refused by `_resolve_declared_adapters` **with a complete artifact emitted** (the observable that distinguishes the two refusal layers, A-139/A-181); and whichever reading of work item 6 is chosen, its exact terminal.

**CA17 — the post-v5 gate composite, run for real.** Install v5, then run the whole registered gate: P33's suite, P26's module at 19 (or 20) tests, the self-hosted lane, `qualify_topos.py` including the release smoke through `release_wheel.py`, and the independent witness. R3-D2's three unenumerated filename sites and R3-B1(c)'s release-manifest consumer are only visible here. This is round 1's CA5 and round 2's CA11 restated a third time, because it has still never been run.

---

## (6) Verdict

**NOT READY.**

The inventory that was supposed to close the class does not close it: I found a live consumer of a frozen expectation inside the registered gate that the sweep misses on two independent grounds, plus three further structural blind spots and a live comparison idiom it cannot see (R3-B1). The consumer the sweep *did* find is absent from the ownership manifest the implementer is told to work from, because that manifest is still frozen at the round-1 commit (R3-B2). The entire config layer — which O2's own observable names — has no frozen oracle (R3-B3), and one of its three work items is unfalsifiable as written (R3-B4). The test added this round to close round 2's third-role finding is built in a way that defeats the suite's own differential rule and never exercises the branch it exists for (R3-B5). AUTHORING's criterion — *"NOT READY if any externally visible decision, interface, example, bound, refusal, or proof source remains for the implementer to invent"* — is met by R3-B3, R3-B4 and R3-B5 independently.

**What is genuinely closed, and should not be re-litigated in round 4.** A-228 is correct and complete: I rebuilt both forgeries as proper differential negatives and confirmed a payload-free `ALL_MUTANTS_EQUIVALENT` is rejected with `'mutation' is a required property` and a complete R3 claim carrying that code is rejected with `'R2' was expected`, while the same claim carrying `CANARY_INCONCLUSIVE` is accepted. The `only-if` closure is complete across all sixteen tier/base combinations, in both directions, including the at-least-one-tier guarantee. The two P25 siblings are exactly the declared projection — `schema_version` 4→5 and `language`/`source_roots`/`base` hoisted `r1`→`resolved`, with nothing else changed — verified by direct diff and independently by a soundly constructed audit test, and both validate against the locked v5 schema. `--check` exits 0, the v4 snapshot is byte-identical to the shipped schema, the suite reproduces 26/4 with four legitimately invariant passes, and all 13 asset digests verify.

**The root cause is unchanged across three rounds, and is now visible as a pattern rather than an incident.** Round 1: the gate runs a locked v4 suite → the carver repaired that suite. Round 2: a second gate step compares against locked v4 templates → the carver built a sweep and repaired that step. Round 3: the sweep's own closure is incomplete, the manifest that records ownership was never regenerated, and the test written to close the last round's finding reproduces the last round's defect shape. Each repair has been correct about the instance and has stopped at the boundary of the instance. The carver named this exactly right — *"an inventory closes a class only if the inventory is right"* — and then shipped an inventory that is checkable, which is real progress, and not yet right.

The productive move for round 4 is narrow: make the sweep's closure sound and pin it with a planted-decoy oracle (CA14), regenerate the manifest at the current anchor, freeze the config-layer negatives (CA16), fix the third-role fixture (CA13), and take CA8 (CA15) rather than defer it a third time. None of those is a design question; all five are mechanical, and four of them are in files already in scope.

---

## Appendix — explicit pass/fail on each item the controller asked me to verify

| item | verdict | evidence |
|---|---|---|
| **Sweep: seeds cover everything the gate hands an interpreter** | **FAIL** | The gate's primary invocation is `assay run tester-unified`, whose argv is in `assay.toml`; the sweep never reads it. `tests/` is seeded only incidentally by the failure-diagnostic rerun (`:187`) and the witness (`:198`). Inline `python - <<PYEOF` heredocs in the gate script are never scanned. |
| **Sweep: reaches every import form** | **FAIL** | `from . import X` (`module=None`) is skipped — 9 occurrences; `safeio.py` provably falls out of the closure. Dotted subpackage paths resolve by leaf name only — `src/assay/adapters/**` and `src/assay/coverage_parsers/**` (11 files) are entirely unreachable. `importlib.import_module` is not handled. |
| **Sweep: component matching is complete** | **FAIL** | The component list is four hardcoded names. `gate/python/release/P25/` matches none, so the frozen release manifest is invisible even to a file inside the closure. The component logic is applied only to frozen-dir detection, never to discovering executable targets. |
| **Sweep: construct a fourth consumer it provably misses** | **DONE — and one is real** | `gate/distribution/release_wheel.py`: executed in the gate by `qualify_topos.py:539-545`, compares against the frozen `gate/python/release/P25/release-manifest.json`, absent from the closure *and* invisible to the component list. Constructible second: any consumer under `src/assay/adapters/`. Also `tests/test_distribution_gate.py` — in the closure, hits `carve-assets`, compares locked assets byte-for-byte, reports zero signals. |
| **A-226 — P25 siblings differ in exactly the declared ways** | **PASS** | Direct diff: only `schema_version` 4→5 and `language`/`source_roots`/`base` moved `r1`→`resolved`. `scrub()` audit test independently sound. Both validate against the locked v5 schema. |
| **P26 — exactly 5 deselected, 19 still assert** | **PARTIAL FAIL** | Counts correct (5 named, all exist, 19 kept) and no kept test is neutered. But `test_all_structural_and_aggregate_bounds_precede_every_git_call` is **not** v4-coupled — it exercises `attestation.load_attested_evidence` and asserts `(ERROR, UNREADABLE_ARTIFACT)`, touching no template/verifier/schema/judgment — so A-210's aggregate-bounds oracle is dropped for no v5 reason. Round 2's own appendix contradicts its table here. |
| **A-228 — reproduce both rejections** | **PASS** | Payload-free → `'mutation' is a required property`; complete R3 claim with that code → `'R2' was expected`; differential control (same claim, `CANARY_INCONCLUSIVE`) accepted. Also rejected on R1. |
| **A-227 — both decisions load-bearing and consistent** | **PARTIAL FAIL** | `only-if`: **PASS**, complete 16/16. `kill_signal_artifact`: the field is *already* refused by the closed `judge.mutation` table, so work item 6 is unfalsifiable as written and `equivalence_artifact`'s identical status is unaddressed. `helpers.role`: A-227's `executable-code` rule is absent from the packet's normative invariant 5, its test is non-differential, and its R1 branch is unexercised. |
| **The `iff` closure — rejects every case it should** | **PASS** | Enumerated all 16 combinations of {r1,r2,r3} × {base present, absent}: every one matches the intended rule, including the at-least-one-tier refusal for a resolved-only judgment. |
| **CA7 deferral legitimate?** | **YES, with an inaccurate reason** | R2 producer coverage genuinely needs a lane the gate lacks. But `qualify_topos.py` *is* a real R0,R1 producer check against a frozen v5 expectation once repointed, so "no producer-side v5 evidence" understates what P33 has. |
| **CA8 deferral legitimate?** | **NO** | `qualify_topos.py` already writes the lane's `base`, already has a `base_override` parameter, and already uses it at `:836`. The file is `implementer_owned`, in `scope.touch`, and edited by work item 8b. A-143's required shape is one scenario plus one assertion away, in the package that collapses two base resolutions into one field. |

---

*Reviewer note: this file was written, not committed. Git and merge mechanics belong to the controller under A-216. No repository file was modified by this review; all probes ran read-only against the working tree or under `/tmp`.*
