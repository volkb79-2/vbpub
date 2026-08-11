# P33 carve assets — carver-owned, implementer must not edit

Re-carved by C-sol-1 after a **NOT READY** pre-dispatch adversarial review
(`../../reports/assay-P33-pre-dispatch-adversarial-review.md`).
Specification: `../../SCHEMA-V5-DESIGN.md`. Decisions: A-220 (shape), A-221
(`go:*` vocabulary), A-222 (locked v4 evidence not rewritten), **A-223** (the
repair set), **A-224** (gate retirement + scope corrections), **A-225** (A-221's
corrected reasoning). Report: `../../reports/assay-P33-JIT-CARVE.md`.

| asset | what it fixes |
|---|---|
| `verdict.schema.v5.json` | **the** new `src/assay/schemas/verdict.schema.json`, installed byte-for-byte |
| `verdict.schema.v4-snapshot.json` | the transform's stable source, byte-identical to the shipped v4 at the input revision. Exists so `--check` survives the migration it verifies (B1) |
| `migrate_v4_to_v5.py` | the auditable v4→v5 delta, 13 logged changes. `--check` must exit 0 before **and after** implementation |
| `test_acceptance_v5.py` | **the locked v5 acceptance suite.** Replaces `carve-assets/P26/test_acceptance.py` in the registered gate (A-224) and carries its attestation coverage forward without editing it. **17 failed / 2 passed** pre-implementation; must reach all green. Every negative is differential |
| `probe_v5_controlled_red.py` | the carver's pre-implementation expectations. Retained, but no longer the acceptance signal — see below |
| `migration-manifest.json` | schema v2, scanned over the **git index**: 19 locked carver-owned, 92 implementer-owned (every one verified inside `scope.touch`), 16 carver-owned prose explicitly excluded |
| `expected/sql-r2-v5-template.json` | the keystone — an `R0,R2` SQL lane, structurally unrepresentable in v4 |
| `expected/ca1-r3-no-base-v5-template.json` | CA1: an `R0,R3` lane whose `judgment.resolved` carries **no** `base`, witnessing A-223(a). Built from the real `tests/fixtures/verdicts/r3_pass.json` shape, not invented |
| `expected/ca4-all-equivalent-v5-template.json` | CA4: `killed 0, survived 0, equivalent 3` → `INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT`, witnessing A-223(d) |
| `expected/missing-tool-v5-template.json` | P27's missing-tool document at v5 |

All four templates carry `@PLACEHOLDER@` tokens and are **not** directly valid —
the same convention as every P25/P26/P27 `expected/*-template.json`. The suite's
and the probe's `SUBS` maps are the authoritative substitution lists.

## Why the probe is no longer the acceptance signal

The first carve cited thirteen green probe expectations as evidence. The review
showed three of them were satisfied by `verify_document`'s version short-circuit
alone (A-138) — the verifier never reached a semantic check on either template —
and that this masking is exactly why three blocking defects survived: a `--check`
that could not pass after the work it verified, a locked template contradicting
A-117's precedence, and a registered gate that reddens on P26's locked v4 suite.
All three are post-implementation-state facts, invisible to any assertion made
against a tree whose shipped schema is still v4.

`test_acceptance_v5.py` is the answer. It runs post-implementation, and every
negative is **differential**: it asserts the unmodified control verifies clean in
the same test that asserts the injected defect does not, so no expectation can
pass on a version mismatch. Pre-implementation it is **17 failed, 2 passed**; the
two passes are the `--check` contract and a pure string assertion over the
templates, both legitimately invariant.

## Reproducing

```sh
# the delta, and that the committed schema was not hand-edited
python3 nyxloom-trove/carve-assets/P33/migrate_v4_to_v5.py --check

# the carver's pre-implementation expectations
PYTHONPATH=src python3 nyxloom-trove/carve-assets/P33/probe_v5_controlled_red.py

# the locked acceptance suite (17 failed / 2 passed before implementation)
PYTHONPATH=src python3 -m pytest \
  nyxloom-trove/carve-assets/P33/test_acceptance_v5.py -q -p no:randomly
```

## Asset hashes

```text
f9f7bc86b316928a752e29ec52b352d51c0bd74ccef1096f60dc1bf5a421af47  expected/ca1-r3-no-base-v5-template.json
f1734e62782558b47799b3f77d933a37c6c63de2fcc4f54f21e226ddb768e408  expected/ca4-all-equivalent-v5-template.json
47a17e5184ab175938ea431ee467c91bf9631747c389bf73d4de949a75ed69c4  expected/missing-tool-v5-template.json
c1544667e2ec25fa9fe22d97598809e0ffe8836a600e7e296c6d9e6120831adb  expected/sql-r2-v5-template.json
c19e061a638e14fe91b954861f6cda62d347fda1d3bf3d380fcea2bbe5e2a8cd  migrate_v4_to_v5.py
3c4e2435487925fd16fb4e9a476c141f5c124c2bc167749d785eb3e17f569ae1  migration-manifest.json
41e57d3208575fae8dc8c7b2e0794ac805ec62d44861df240f36e01207a70d3f  probe_v5_controlled_red.py
a103a1c3e8d822224e2eba480e832422b8843a6132b510d59a4748a7d300d52d  test_acceptance_v5.py
4e8bcbf46eca1836e52502114c6583a7dc1af88d85eff6772e837a9b9a1c3df0  verdict.schema.v4-snapshot.json
de180687aea45e419995a7a528da443e13e65465350a62cc0143486517b6e61e  verdict.schema.v5.json
```

`README.md` is excluded because it carries the list.
