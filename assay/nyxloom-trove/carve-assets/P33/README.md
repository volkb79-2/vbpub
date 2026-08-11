# P33 carve assets — carver-owned, implementer must not edit

Re-carved by C-sol-1 after **three** NOT READY pre-dispatch adversarial reviews
(`../../reports/assay-P33-pre-dispatch-adversarial-review.md`, `...-round2.md`,
`...-round3.md`).
Specification: `../../SCHEMA-V5-DESIGN.md`. Decisions: A-220 (shape), A-221
(`go:*` vocabulary), A-222 (locked v4 evidence not rewritten), **A-223** (the
repair set), **A-224** (scope corrections), **A-225** (A-221's corrected reasoning),
**A-226**/**A-227**/**A-228** (round 2), **A-229** (round 3). Report: `../../reports/assay-P33-JIT-CARVE.md`.

| asset | what it fixes |
|---|---|
| `verdict.schema.v5.json` | **the** new `src/assay/schemas/verdict.schema.json`, installed byte-for-byte |
| `verdict.schema.v4-snapshot.json` | the transform's stable source, byte-identical to the shipped v4 at the input revision. Exists so `--check` survives the migration it verifies (B1) |
| `migrate_v4_to_v5.py` | the auditable v4→v5 delta, 13 logged changes. `--check` must exit 0 before **and after** implementation |
| `test_acceptance_v5.py` | **the locked v5 acceptance suite**, 38 tests. Runs alongside P26's module, which is deselected by **four** tests rather than retired (A-226/A-229). **30 failed / 8 passed** pre-implementation; must reach all green. Every negative is differential |
| `probe_v5_controlled_red.py` | the carver's pre-implementation expectations. Retained, but no longer the acceptance signal — see below |
| `migration-manifest.json` | schema v2, scanned over the **git index**: 19 locked carver-owned, 92 implementer-owned (every one verified inside `scope.touch`), 16 carver-owned prose explicitly excluded |
| `expected/sql-r2-v5-template.json` | the keystone — an `R0,R2` SQL lane, structurally unrepresentable in v4 |
| `expected/ca1-r3-no-base-v5-template.json` | CA1: an `R0,R3` lane whose `judgment.resolved` carries **no** `base`, witnessing A-223(a). Built from the real `tests/fixtures/verdicts/r3_pass.json` shape, not invented |
| `expected/ca4-all-equivalent-v5-template.json` | CA4: `killed 0, survived 0, equivalent 3` → `INCONCLUSIVE/ALL_MUTANTS_EQUIVALENT`, witnessing A-223(d) |
| `expected/missing-tool-v5-template.json` | P27's missing-tool document at v5 |

All six templates carry `@PLACEHOLDER@` tokens and are **not** directly valid —
the same convention as every P25/P26/P27 `expected/*-template.json`. The suite's
and the probe's `SUBS` maps are the authoritative substitution lists.

## Why the probe is no longer the acceptance signal

The first carve cited thirteen green probe expectations as evidence. The review
showed three of them were satisfied by `verify_document`'s version short-circuit
alone (A-138) — the verifier never reached a semantic check on either template —
and that this masking is exactly why three blocking defects survived: a `--check`
that could not pass after the work it verified, a locked template contradicting
A-117's precedence, and a registered gate that reddens on P26's locked v4 suite. Round 2 then found the
same class at the next gate step, which is why `sweep_v4_consumers.py` exists.
All three are post-implementation-state facts, invisible to any assertion made
against a tree whose shipped schema is still v4.

`test_acceptance_v5.py` is the answer. It runs post-implementation, and every
negative is **differential**: it asserts the unmodified control verifies clean in
the same test that asserts the injected defect does not, so no expectation can
pass on a version mismatch. Pre-implementation it is **26 failed, 4 passed**; the passes are the `--check`
contract, the pure string assertion over the templates, and the two P25 sibling
projection audits — all legitimately invariant.

## Reproducing

```sh
# the delta, and that the committed schema was not hand-edited
python3 nyxloom-trove/carve-assets/P33/migrate_v4_to_v5.py --check

# the carver's pre-implementation expectations
PYTHONPATH=src python3 nyxloom-trove/carve-assets/P33/probe_v5_controlled_red.py

# the consumer inventory -- attack this first
python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py

# the locked acceptance suite (30 failed / 8 passed before implementation)
PYTHONPATH=src python3 -m pytest \
  nyxloom-trove/carve-assets/P33/test_acceptance_v5.py -q -p no:randomly
```

## Asset hashes

```text
f9f7bc86b316928a752e29ec52b352d51c0bd74ccef1096f60dc1bf5a421af47  expected/ca1-r3-no-base-v5-template.json
f1734e62782558b47799b3f77d933a37c6c63de2fcc4f54f21e226ddb768e408  expected/ca4-all-equivalent-v5-template.json
47a17e5184ab175938ea431ee467c91bf9631747c389bf73d4de949a75ed69c4  expected/missing-tool-v5-template.json
e4378a0e85189b9f9b2c59184df0760bcbfeb7efbc01e44417af0b960ac128e7  expected/p25-missing-v5-template.json
7d2685455f70f8e7d4a2d55deba4aff1e6b4799d9e885ca7bda5bf8da985dea1  expected/p25-pass-v5-template.json
c1544667e2ec25fa9fe22d97598809e0ffe8836a600e7e296c6d9e6120831adb  expected/sql-r2-v5-template.json
e63577513ab78c284f9d3e2efe2d3f4af8c884829fcb8060be94143c3be5ee3f  migrate_v4_to_v5.py
15e5c4d3438c441b60cc093a8af24b4956a5a04f6fc9cae03f9029b8cb785874  migration-manifest.json
41e57d3208575fae8dc8c7b2e0794ac805ec62d44861df240f36e01207a70d3f  probe_v5_controlled_red.py
e54ee4703e74741b591584cd6adc3f4306f3da6c3eddd59e18c5a227e78e4875  sweep_v4_consumers.py
7d5d76998cca55810882dbcf01ffe6620ac7e8e7f43151fdedc2dc9d0b1a8676  test_acceptance_v5.py
4e8bcbf46eca1836e52502114c6583a7dc1af88d85eff6772e837a9b9a1c3df0  verdict.schema.v4-snapshot.json
0e894676b4114a73cc8146b16e33a6ca99920a1cf3ac4be773ca620763e1b50a  verdict.schema.v5.json
```

`README.md` is excluded because it carries the list.

## Round-3 additions

| asset | what it fixes |
|---|---|
| `sweep_v4_consumers.py` **v2** | closes the five closure gaps round 3 demonstrated: seeds from `assay.toml`'s real argv + inline heredocs, follows subprocess targets, resolves dotted modules through the package layout, handles `from . import X`, and replaces the name/regex predicate with "reads a frozen tree by any idiom and compares". Adds `indirect-path-from-caller` for `release_wheel.py`, whose frozen path arrives on its command line and which **no name-based predicate could ever find**. Closure 147 → 189, consumers 11 → 40 |
| `test_acceptance_v5.py` | now 38 tests, **30 failed / 8 passed** pre-implementation. New: the planted-decoy oracle that pins the sweep itself, the five frozen consumers, the closure boundary, three config-layer negatives, the manifest check, and the rebuilt third-helpers-role differential |
| `migration-manifest.json` | schema v3, regenerated at the current anchor and **unioned with the sweep's own output**, so inventory and ownership cannot drift apart |

The decoy is reached by a real dotted-subpackage import edge. A decoy nothing
imports would prove nothing — it genuinely is not in the gate's execution path,
and the sweep would be right to ignore it.
