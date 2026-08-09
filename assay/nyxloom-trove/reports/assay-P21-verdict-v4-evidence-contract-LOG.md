# assay-P21 — implementation LOG

> **Result: DONE**, after one mechanical BLOCKED exit and a routed carver
> correction (A-183). This LOG is APPENDED to, never rewritten: leg 1's
> block is the record of why the contract changed, and deleting it would
> hide the most useful thing this package produced (A-072's own discipline,
> and canonical DOCTRINE §6's "append the outcome rather than editing the
> original entries").
>
> **Implementer:** one Opus xhigh child, forked from the package-neutral
> frozen orientation base at `20beeda152a08114692fb846aee6dc0118f1e86a` and
> RESUMED in place after the correction merge — not a second child.
> **Worktree:** `/workspaces/vbpub/.worktrees/assay-P21-verdict-v4-evidence-contract`
> **Branch:** `feat/assay-P21-verdict-v4-evidence-contract`
> **Date:** 2026-08-09
>
> | leg | anchor | outcome |
> |---|---|---|
> | 1 | `20beeda1` | BLOCKED — `71b1b961` (see below) |
> | correction | — | `ddad505b` carve + `76ecc814` merge (A-183) |
> | 2 | `76ecc814` | implemented, this commit |

---

# Leg 1 — BLOCKED (historical, preserved verbatim)

`BLOCKED: the frozen packet's mandatory deletion of the old full-text mutation
surface cannot be performed without editing src/assay/adapters/go.py, which is
in scope.forbid — and the replacement protocol supplies no successor for the
"UNSUPPORTED" sentinel that is the Go adapter's only truthful mutation
terminal.`

Two coupled defects, both reproduced against the repository rather than
inferred. They share one amendment, so they are reported together.

### 1. The deletion requires a forbidden file

The implementation packet states, under *Owned interfaces* and again in work
item 4:

> The old `Mutant`, `generate_mutants`, `collect_mutants`, and full-text
> identity are **deleted, not retained as compatibility surfaces**. Go R2 is
> not registered; `go.py` remains untouched until P29 implements this
> already-landed protocol.

`go.py` cannot remain untouched across that deletion. It holds an
unconditional module-level import of the deleted name:

- `src/assay/adapters/go.py:159` — `from ..mutation import Mutant`
- `src/assay/adapters/go.py:518-520` — `def generate_mutants(...) -> tuple[Mutant, ...] | Literal["UNSUPPORTED"]`

`from __future__ import annotations` (line 154) defers the *annotation* only;
the `import` statement still executes at module load.

**Reproduction** (disposable copy of `src/`, real tree never modified; the copy
was deleted afterwards). The only edit was renaming the class exactly as work
item 4 instructs:

```text
$ PYTHONPATH=<copy>/src python -c "import assay.adapters.go"
  File "<copy>/src/assay/adapters/go.py", line 159, in <module>
    from ..mutation import Mutant
ImportError: cannot import name 'Mutant' from 'assay.mutation'

$ PYTHONPATH=assay/src python -c "import assay.adapters.go; print('control import OK')"
control import OK
```

Blast radius: `assay.adapters.go` becomes unimportable, so the **12 test
modules that import `GoAdapter`** fail at collection. No *production* module
imports `adapters.go` (verified: `registry.py`, `canary.py`, `cli.py`,
`adapters/base.py` mention it only in prose), so this is a suite-wide
collection break rather than a runtime product break — but it is a break, and
it lands in a file this package may not touch.

`adapters/base.py:41` and `adapters/python.py:163` hold the same import and are
both in `scope.touch`, so they are not part of this block. **`go.py` is the
only forbidden file involved.**

### 2. The new protocol has no successor for `UNSUPPORTED`

This is why the block is not merely "widen `scope.touch` by one file".

The packet's replacement signature returns a plain tuple:

```python
def generate_mutation_sites(
    self, text: str, lines: set[int], *,
    operators: tuple[str, ...], limit: int,
) -> tuple[MutationSite, ...]: ...
```

and fixes exactly two terminals for it — *"Invalid Python syntax raises
`MutationDiscoveryError`; valid syntax with zero sites returns `()`"*, with
A-171 binding `()` to `INCONCLUSIVE/NO_MUTANTS` and `MutationDiscoveryError` to
`ERROR/MUTATION_DISCOVERY_FAILED`.

The `Literal["UNSUPPORTED"]` member of the old union is deleted with it. That
sentinel is the **only** vocabulary `GoAdapter` has for *"this adapter has no
mutation engine at all"*, and it is load-bearing prior product truth:

- **A-042 / A-087** — no Go toolchain exists anywhere in this devcontainer, so
  no generated Go mutant can be proven valid; `GoAdapter.generate_mutants`
  returns `UNSUPPORTED` unconditionally.
- **A-114** — `UNSUPPORTED` is pinned as a whole-adapter-call union member,
  explicitly *"never a per-construct union"*.
- **A-011** — `UNSUPPORTED` renders `INCONCLUSIVE_NO_MUTANTS`, **never green**.

Neither surviving terminal is truthful for Go. Returning `()` asserts *"this
file was validly analysed and has nothing mutable"* — a positive measurement
claim about Go source that no Go analysis was performed to support, which is
the lane-table-implies-capability failure this project exists to remove.
Raising `MutationDiscoveryError` asserts a discovery **boundary failure**
(`ERROR`), which is equally false: nothing failed, the capability was never
built.

So even with `go.py` added to `scope.touch`, what `GoAdapter` exposes after
this migration is an **unspecified, externally visible decision**. Contract
class 2b assigns that to the carver — *"Implementer freedom: private
construction only. Names, types, requiredness, operator/reason vocabularies,
migration behavior, and cross-field invariants are fixed"* — and AUTHORING's
own pre-dispatch review requires NOT READY when *"any externally visible
decision, interface, example, bound, refusal, or proof source remains for the
implementer to invent"*. I am not authorised to invent it, and inventing it
would silently retire A-042/A-011's honest terminal.

## Why no partial implementation was landed

Work item 1 requires the v4 bump *"in one atomic migration"*, and A-180 is
explicit that the bounded site seam must land **with** the cap it makes true:

> Deferring the repair to P23 would ship two packages with a knowingly false
> v4 cap.

Landing work items 1-3 and 5-10 while leaving the adapter seam on the old
full-text surface would ship exactly that false cap — `Mutation.candidate_count`
and `judgment.r2.max_mutants` advertising a bound no discovery path enforces.
That is the specific defect this JIT carve was performed to prevent, so a
partial landing is worse than none. Per the BLOCKED rule and canonical
DOCTRINE §3, nothing was improvised.

## What was done before stopping

State reconciliation (all mechanical, all passed):

```text
git rev-parse HEAD                    20beeda152a08114692fb846aee6dc0118f1e86a
merge-base --is-ancestor anchor HEAD  yes (anchor == HEAD)
branch                                feat/assay-P21-verdict-v4-evidence-contract
git status --porcelain                clean (0 lines)
git diff --name-status <anchor>..HEAD (empty, as the dispatch predicted)
git diff <anchor> -- assay/src assay/tests assay/assay.toml assay/pyproject.toml
                                      (empty)
```

Locked skeleton applied and the locked acceptance run once, per the dispatch
recipe, to witness the intended controlled red:

```text
$ git apply assay/nyxloom-trove/carve-assets/P21/skeleton.patch
APPLY=ok
$ python -m py_compile assay/src/assay/output.py
COMPILE=ok
$ PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
    assay/nyxloom-trove/carve-assets/P21/test_acceptance.py -q
24 failed in 0.80s
```

**24 failed, zero collection/setup errors — exactly the count and shape
`reports/assay-P21-JIT-CARVE.md` witnessed (`24 failed in 0.85s`).** The locked
packet's own baseline reproduces faithfully at this anchor; the block is in the
packet's mutation-seam contract, not in its proof assets.

The applied skeleton was then **removed**, so the branch carries no
half-implemented `output.py`. The controller reapplies it on redispatch. Every
carve-asset under `nyxloom-trove/carve-assets/P21/` is byte-for-byte unchanged
(verified against the eight SHA-256 hashes recorded in the JIT-CARVE report).

## Amendment needed to unblock

One bounded carver decision, then redispatch. Both parts are needed; part (b)
alone leaves the import broken, part (a) alone leaves Go's terminal invented.

**(a)** Add `src/assay/adapters/go.py` to `scope.touch` (it stays out of
`scope.forbid`), limited to the two lines the deletion forces: the
`..mutation` import and the mutation method's signature.

**(b)** Rule what `GoAdapter` exposes for mutation between P21 and P29, as a
`D-`/`A-` decision, not an implementer choice. The three candidates I can see,
with what each costs:

1. **Retain a capability terminal on the new seam** — e.g. a
   `MutationUnsupported` marker or an `unsupported: bool` on the adapter,
   distinct from both `()` and `MutationDiscoveryError`, preserving
   A-011/A-114's honest `INCONCLUSIVE/NO_MUTANTS`. Keeps prior truth; costs one
   more shape in the frozen v4 seam.
2. **Drop the mutation method from `GoAdapter` entirely** — Go is unregistered
   for R2, so nothing calls it; the capability question moves to registry
   preflight, where A-013/A-142/A-144 already put "prerequisite absent". Costs
   a `LanguageAdapter` protocol that `GoAdapter` no longer structurally
   satisfies in full.
3. **Declare Go's mutation prerequisite missing** — render
   `NO_MEASUREMENT/MISSING_EXTERNAL_TOOL`, which P21 already reserves for P27.
   Cleanest semantically; but P21 reserves that reason explicitly *for P27's
   first real external-tool preflight*, so using it here changes what P21's own
   reservation means.

I have deliberately **not** chosen between these. Each is externally visible
and each rewrites an already-decided terminal.

Everything else in the packet was read and is implementable as specified: the
three complete v4 documents, the fourteen invalid cases with their exact
per-layer ownership, the UTF-8 site manifest (offsets independently
recomputed against the declared text and confirmed correct — `<` at bytes
36/40, `and` at 44-47, `True` at 48-52, with the two-byte `π` prefix accounted
for), the max+1 zero-submission sentinel, the output reservation state machine,
and the `HEAD_CHANGED`/`DIRTY_TREE` precedence repair.

---

# Leg 2 — implemented against the A-183 correction

## Reconciliation

The same child was resumed in place. All five checks passed before anything
was read:

```text
worktree                 .worktrees/assay-P21-verdict-v4-evidence-contract
branch                   feat/assay-P21-verdict-v4-evidence-contract
git rev-parse HEAD       76ecc814bfcf4a0e6b759bf3d295f7544e42e29a
anchor 20beeda1          ancestor of HEAD           yes
blocked commit 71b1b961  ancestor of HEAD           yes
```

`git diff --name-status 20beeda1..HEAD` showed 13 files: the handoff, the
amended JIT report, `decisions.md` (A-183), `STATE.md`, `DESIGN-GUIDE.md`,
the P29 handoff, the series README, the pre-dispatch review, the four
changed/added locked assets, and leg 1's own LOG. **`assay/src` and
`assay/tests` were untouched**, so the product tree was still exactly the
pristine anchor state.

A-183 grants precisely what leg 1 said was missing, and nothing more: `go.py`
enters `scope.touch` for the forced import/signature migration only, and the
adapter-wide `"UNSUPPORTED"` marker survives the bounded seam as a
payload-free `INCONCLUSIVE/MUTATION_UNSUPPORTED`. I did not widen it further.

## Controlled red, witnessed at the corrected tree

```text
$ git apply assay/nyxloom-trove/carve-assets/P21/skeleton.patch      ok
$ python -m py_compile assay/src/assay/output.py                     ok
$ PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
    assay/nyxloom-trove/carve-assets/P21/test_acceptance.py -q
28 failed in 0.85s
```

**28 failed, zero collection/setup errors** — exactly the count the amended
JIT report witnesses (`All 28 are controlled red on the exact anchor`).

## What was built

| work item | where | note |
|---|---|---|
| 1 v4 migration | `verdict.py`, `schemas/verdict.schema.json`, `verify.py`, 38 fixtures, 4 installed-wheel witnesses | one atomic bump; v1-v3 rejected with one version-only diagnostic, checked BEFORE required-field inspection |
| 2 one vocabulary | new `vocabulary.py` leaf | imported by config, mutation, model, raw verifier; deletes `config.py`'s deferred-import cycle workaround and closes `MutantOutcome.operator` + `judgment.r2.operators` in the model for the first time |
| 3 killed identities | `verdict.py`, `mutation.py` | `killed` is an identity list; identity is `(path,start_byte,end_byte,replacement_sha256,operator)`, built from the validated site, never a text diff; unique within AND across all four buckets |
| 4 bounded seam + cap | `adapters/{base,python,go}.py`, `mutation.py`, `config.py` | `MutationSite`/`MutationDiscoveryError`/`collect_mutation_sites`; Python retains at most `limit` descriptors via a max-heap while walking; `max_mutants` required in `1..10_000`; Go migrated to the new signature, still unconditionally `"UNSUPPORTED"` |
| 5 canary target | `canary.py`, `config.py`, `verdict.py`, `verify.py` | all six `CanaryResult` sites; target normalized at config load so model and policy compare one spelling |
| 6 exclusion capability | `coverage.py`, `evaluate.py`, `verdict.py` | `derive_exclusion_capability` from unanimous parser evidence; mixed profile is `ERROR/UNREADABLE_ARTIFACT` |
| 7 interval ordering | `verdict.py`, `verify.py` | parsed offset-aware instants in model + raw verifier; schema deliberately NOT credited |
| 8 output reservation | new `output.py`, `cli.py`, `runner.py` | descriptor-held parent, no temp across execution, appeared/changed destination preserved; `write_verdict` now only serialises |
| 9 adversarial artifacts | fixtures + 4 new test modules | see the controlled-break table |
| 10 HEAD_CHANGED | `runner.py`, `errors.py` | one `dirty_paths` call, `head_rev` only on the clean branch; dirt keeps precedence |

## Two judgement calls worth flagging to the reviewer

1. **`run_mutation`'s per-bucket sorts were deleted, not kept.** Once
   `collect_mutation_sites` refuses an out-of-order batch and visits targets
   by path — and `MutantOutcome.identity` leads with `path` — no input the
   collector can produce would change under those sorts. AUTHORING §3b.D asks
   for an unreachable line to be restructured away rather than kept and never
   exercised. The invariant is not weakened: `Mutation` still REFUSES an
   out-of-order bucket, so a future regression raises instead of emitting a
   scrambled artifact. `test_mutation_isolation.py`'s ordering test was
   rewritten to assert the cross-file property that IS observable.
2. **`judge.canary.target` is normalized at config load.** The packet requires
   `CanaryResult.target` to be "the normalized declared project-relative
   string" and to EQUAL `judgment.r3.target`. Config validated containment but
   never the spelling, so a legal `./src/p.py` would have reached the model's
   wire grammar and raised an uncaught `ValueError` in a producer path.
   Normalizing at the one boundary that reads it makes the equality mean what
   it says and keeps a legal lane from crashing a run.

## A-067 controlled breaks

Each break was applied to the real tree, run against the narrowest
discriminating selection, then reverted; `grep` confirms none survives.

| # | break | tests run | red |
|---|---|---|---|
| 1 | `MutantOutcome.identity` reverts to line+description | mutation payload + artifacts | **7** |
| 2 | `JudgmentR2` stops closing its operator vocabulary | judgment + locked | **2** |
| 3 | `run_mutation` discovers with `max_mutants + 1000` | locked + collect | **1** |
| 4 | `Verdict` stops comparing canary target to policy | locked + judgment | **1** |
| 5 | `derive_exclusion_capability` always returns `reported` | capability + four-way union | **2** |
| 6 | `Verdict` stops comparing the parsed instants | interval + locked | **2** |
| 7 | version check moved after required-field inspection | locked + conformance | **3** |
| 8 | CLI reserves output AFTER the lane runs | locked + CLI | **1** |

Break 2 is the informative one: only two tests went red because the SCHEMA
and `MutantOutcome`'s own closure still reject the same artifact. That is
layer redundancy working as A-182 intends, not a hole — the locked
`mutation-agreeing-unknown-operator` case survived precisely because two
other layers caught it.

## Evidence

```text
$ PYTHONPATH=src python -m pytest --override-ini=pythonpath= \
    nyxloom-trove/carve-assets/P21/test_acceptance.py -q
28 passed in 0.80s

$ PYTHONPATH=src python -m pytest tests -q --override-ini=pythonpath=
2098 passed, 1 skipped in 98.69s
```

The single skip is the documented `ASSAY_SELF_HOSTING_VERDICT` case, which
skips without the gate's own first-step artifact (STATE.md's standing note).
Both runs are DEVCONTAINER runs and therefore diagnostic, not a ship signal.

```text
handoff lint        only the intentional L10 size warning (A-089)
locked assets       byte-identical to HEAD (git status on carve-assets: empty)
```

The installed-wheel witnesses were migrated deliberately, not mechanically:
`test_standalone.py`'s expected mutant identities are derived by scanning the
LITERAL fixture text and hashing the literal replacement bytes
(`_gt_site`), never read back from assay's own output — A-041's rule that
assay may not generate its own expected artifacts.

**No registered `tester-unified` gate run was performed** — the controller
owns that, and this LOG claims nothing about it.

---

# Leg 3 — independent review (R-opus-1), phases 1 and 2

Appended, never rewritten (DOCTRINE §6): legs 1 and 2 above are the
implementer's own record and stay exactly as written.

Reviewer: fresh Opus xhigh child forked from the package-neutral frozen
reviewer base `R-opus-1`, oriented at
`20beeda152a08114692fb846aee6dc0118f1e86a`. Phase 1 was blind — the
implementation report, transcript, and controller receipts were not read
until Phase 1 was closed.

## Phase 1 — blind findings (disposition FIX)

Reviewed at implementation HEAD `5bf87de225932bb4b35df84d9ab82ff9ffd93e43`,
worktree clean, all nine locked hashes verified by the reviewer, `go.py`
confirmed limited to A-183's forced import/signature migration, and the
BLOCKED leg confirmed intact.

| # | Severity | Finding |
|---|---|---|
| 1 | HIGH | `assay run` emits a payload-free `ERROR/MUTATION_DISCOVERY_FAILED` R2 artifact that its own `assay verify` REJECTS. The model declares the shape legal (`Claim._check_mutation_terminal_correspondence`); `verify._check_r2_rederivation` compared it against the PASSing R0 baseline and disagreed. Five further payload-free R2 terminals were equally affected. |
| 2 | MEDIUM | The conformance vocabulary audit did not move with the capability (A-141's own shape). P21 took the closed vocabulary to 26 pairs; `test_verdict_conformance.py` still transcribed 19 and asserted the literal `len(ALL_PAIRS) == 19`, so five newly-reachable terminals were silently exempt from the fixture requirement. This is the root cause of finding 1. |
| 3 | MEDIUM | The raw operator-policy sweep in `verify._check_judgment_matches_claims` visited `survived`/`crashed`/`budget_exceeded` but not `killed` — the one bucket work item 3 exists to bind (A-180). Caught only by model reconstruction, so the independent second witness A-182 assigns to the raw layer was absent. |
| 4 | MEDIUM | No complete-artifact witness existed for four reachable new terminals. |
| 5 | LOW | `run_mutation`'s docstring still claimed `operators` is "not re-validated" and "degrades honestly, matching nothing"; `collect_mutation_sites` raises `ValueError`. |

Why a green suite could not see findings 1 and 3: the locked negatives assert
`bool(verify.verify_document(document))`, and that list merges raw failures
with model-reconstruction failures — "the raw layer caught it" and "only the
model did" are indistinguishable to every locked case.

Phase 1 combined-axis attacks that PASSED and are recorded as evidence the
contract holds: clean post-command HEAD move with the verdict reserved inside
the consumer repository (artifact names the pre-run commit, verify-clean, zero
probe residue); relative `--verdict-json` resolving in the CLI process cwd and
not `project_root`; cross-file identical byte-sites staying distinct and
path-ordered; mixed-capability adapter behaviour.

## Phase 2 — repairs applied by the reviewer

Every finding was independently reproduced against the real tree before any
edit. Repairs are bounded to files already in P21's `scope.touch`.

1. **`src/assay/verify.py::_check_r2_rederivation`** — new
   `_INDEPENDENT_R2_TERMINALS` names the six reasons `run_lane` renders as a
   payload-free R2 claim for R2's OWN cause (`MUTATION_DISCOVERY_FAILED`,
   `DIRTY_TREE`, `HEAD_CHANGED`, `BASE_IS_HEAD`, `GIT_FAILED`,
   `UNREADABLE_ARTIFACT`). For those the STATUS is re-derived from the closed
   vocabulary via `_outcome_owning` — DERIVED from `REASON_CODES`, not a
   second hand-maintained table — so this is not a blanket accept: a forged
   `FAIL/DIRTY_TREE` or `ERROR/BASE_IS_HEAD` is still rejected. The
   `MUTATION_UNSUPPORTED` mapping and the baseline-propagation path for every
   other reason are preserved unchanged. `refuse_lane`'s `BAD_LANE_CONFIG` is
   deliberately excluded: it renders the identical pair on every level
   including R0, so the existing baseline comparison already re-derives it.
2. **`src/assay/verify.py`** — `killed` added to the raw operator-policy sweep.
3. **`tests/test_verdict_conformance.py`** — `VOCABULARY` taken to the actual
   26 pairs; the stale literal replaced by a MECHANICAL set comparison against
   `test_errors.EXPECTED_REASON_CODES`, the sibling HAND transcription of the
   same DESIGN-GUIDE §6 table (never `assay.errors.REASON_CODES`, preserving
   the module's independence argument). `EXCLUDED_ENTIRELY` now carries three
   pairs with a written argument each — `OUTPUT_WRITE_FAILED` (structurally
   artifact-less: it means the artifact could not be written), and P22's
   `SNAPSHOT_LIMIT_EXCEEDED` and P27's `MISSING_EXTERNAL_TOOL` (reserved by
   A-163, unreachable in this build). Each names the package that must remove
   its line and add the fixture.
4. **Four hand-authored complete fixtures** under `tests/fixtures/verdicts/`:
   `r2_error_mutation_discovery_failed`, `r2_inconclusive_mutation_unsupported`,
   `r2_budget_exceeded_mutant_limit_exceeded`, `r2_no_measurement_head_changed`.
5. **`tests/test_verify_layer_independence.py`** (new) — raw-layer-only tests
   calling `verify._check_judgment_matches_claims` DIRECTLY (because
   `verify_document` cannot witness layer independence), the six payload-free
   terminals, their foreign-outcome negatives, the preserved
   baseline-propagation path, and the producer-to-verify round trip through
   the real CLI for the discovery terminal.
6. **`src/assay/mutation.py`** — the stale `run_mutation` operator-policy
   docstring corrected.

## Phase 2 evidence

```text
$ PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
    assay/nyxloom-trove/carve-assets/P21/test_acceptance.py -q
28 passed in 0.50s

$ PYTHONPATH=src python -m pytest --override-ini=pythonpath= tests -q
2129 passed, 1 skipped in 88.77s
```

2098 -> 2129 is exactly the 31 tests this leg adds. The single skip is the
documented `ASSAY_SELF_HOSTING_VERDICT` case.

A-067 controlled breaks — each applied to the real tree, measured, reverted,
and the revert verified (`git status` shows no break residue):

```text
break 1  killed removed from the raw sweep .................. 2 red
break 2  payload-free terminal branch disabled .............. 9 red
         (incl. the producer->verify round trip and two fixture round-trips)
break 3  one new reason dropped from the transcribed table ... 2 red
         (the old literal `== 19` stayed GREEN under this exact break)
break 4  an artifact-less pair un-excluded .................. 1 red
break 5  a required fixture removed ......................... 1 red
```

Break 3 is the informative one: it is the precise drift that shipped, and the
mechanical comparison now catches it where the literal could not.

Locked assets re-verified after repair: all nine SHA-256 unchanged. No
carve asset, handoff, `pyproject.toml`, gate script, `assay.toml`,
`nyxloom.toml`, `conftest.py`, or P22 file was touched.

**No registered `tester-unified` gate run was performed in either phase.** The
controller owns that, and this leg claims nothing about it. Both suites above
are devcontainer runs and are diagnostic only.

**Disposition: PASS after reviewer repair.** Findings 1-5 fixed and witnessed.
