# P33 carve assets — carver-owned, implementer must not edit

Re-carved by C-sol-1 after **five** NOT READY pre-dispatch adversarial reviews
(`../../reports/assay-P33-pre-dispatch-adversarial-review.md`, `...-round2.md`,
`...-round3.md`, `...-round4.md`, `...-round5.md`) — round 4 also carried a one-time independent
second opinion from a different model family.
Specification: `../../SCHEMA-V5-DESIGN.md`. Decisions: A-220 (shape), A-221
(`go:*` vocabulary), A-222 (locked v4 evidence not rewritten), **A-223** (the
repair set), **A-224** (scope corrections), **A-225** (A-221's corrected reasoning),
**A-226**/**A-227**/**A-228** (round 2), **A-229** (round 3), **A-230**/**A-231** (round 4), **A-232**/**A-233** (round 5). Report: `../../reports/assay-P33-JIT-CARVE.md`.

| asset | what it fixes |
|---|---|
| `verdict.schema.v5.json` | **the** new `src/assay/schemas/verdict.schema.json`, installed byte-for-byte |
| `verdict.schema.v4-snapshot.json` | the transform's stable source, byte-identical to the shipped v4 at the input revision. Exists so `--check` survives the migration it verifies (B1) |
| `migrate_v4_to_v5.py` | the auditable v4→v5 delta, 13 logged changes. `--check` must exit 0 before **and after** implementation |
| `test_acceptance_v5.py` | **the locked v5 acceptance suite.** Runs alongside P26's module, deselected by four tests rather than retired (A-226/A-229). Counts: `migration-manifest.json` → `CANONICAL_COUNTS`. Every negative is differential |
| `probe_v5_controlled_red.py` | the carver's pre-implementation expectations. Retained, but no longer the acceptance signal — see below |
| `migration-manifest.json` | schema v4, scanned over the git index and unioned with the sweep. Carries **`CANONICAL_COUNTS`, the single source of truth for every number in this package** — round 4 found four different count pairs across four documents, so no document restates one independently any more |
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
pass on a version mismatch. Its pre-implementation state is recorded once, in `CANONICAL_COUNTS`.

## Reproducing

```sh
# the delta, and that the committed schema was not hand-edited
python3 nyxloom-trove/carve-assets/P33/migrate_v4_to_v5.py --check

# the carver's pre-implementation expectations
PYTHONPATH=src python3 nyxloom-trove/carve-assets/P33/probe_v5_controlled_red.py

# the consumer inventory -- attack this first
python3 nyxloom-trove/carve-assets/P33/sweep_v4_consumers.py

# the locked acceptance suite (pre-implementation state: CANONICAL_COUNTS)
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
6403cba2678d80c07a159a5c3cc0a0092c04beff8874241c523f094cfb86949a  migrate_v4_to_v5.py
ca5bb08290e668a52b2ab9435b942151d1bc9c70e8161517619788c6fc2556fd  migration-manifest.json
41e57d3208575fae8dc8c7b2e0794ac805ec62d44861df240f36e01207a70d3f  probe_v5_controlled_red.py
262899cca2bb7ed667bd248b41473535d4ceae07068546f644f695f3dfb9e2a1  sweep_v4_consumers.py
aa9de487724493da3a80accc02f93ee3475a64e447fa653f219c2539ae5dcbe0  test_acceptance_v5.py
4e8bcbf46eca1836e52502114c6583a7dc1af88d85eff6772e837a9b9a1c3df0  verdict.schema.v4-snapshot.json
2706b213b3d3a22d05ec660bc09e25d243d73d9dd8590a48009924ba39aac39b  verdict.schema.v5.json
```

`README.md` is excluded because it carries the list.

## Round-3 additions

| asset | what it fixes |
|---|---|
| `sweep_v4_consumers.py` **v2** | closes the five closure gaps round 3 demonstrated: seeds from `assay.toml`'s real argv + inline heredocs, follows subprocess targets, resolves dotted modules through the package layout, handles `from . import X`, and replaces the name/regex predicate with "reads a frozen tree by any idiom and compares". Adds an indirect category for `release_wheel.py`, whose frozen path arrives on its command line and which **no name-based predicate could ever find**. Closure and consumer counts: `CANONICAL_COUNTS` |
| `test_acceptance_v5.py` | the planted-decoy oracle that pins the sweep itself, the frozen consumer set, the closure boundary, config-layer negatives, the manifest check, and the rebuilt third-helpers-role differential. Counts: `CANONICAL_COUNTS` |
| `migration-manifest.json` | schema v3, regenerated at the current anchor and **unioned with the sweep's own output**, so inventory and ownership cannot drift apart |

The decoy is reached by a real dotted-subpackage import edge. A decoy nothing
imports would prove nothing — it genuinely is not in the gate's execution path,
and the sweep would be right to ignore it.

## Round-4 additions

| change | what it fixes |
|---|---|
| `sweep_v4_consumers.py` **v3** | bare-token matching removed (12 false positives, and it was misclassifying a real environ-sourced consumer as `direct`); `indirect-path-from-environ` is now its own named, separately-tested category; an argv entry is only a finding when another closure member actually supplies it a frozen path, which removes the `verify.py` noise without losing `release_wheel.py`. Consumer counts: `CANONICAL_COUNTS` |
| `test_acceptance_v5.py` | config tests call the **real** `load_lane_file` and pin `LaneConfigError`; a symbol-existence test holds the API surface; the gate script itself is now checked at source level, so O5's wiring claim has an oracle; the environ category and the indirect-noise rule each have a test |
| `migration-manifest.json` | schema v4, with `CANONICAL_COUNTS` as the one place numbers live |

## Round-5 additions

| change | what it fixes |
|---|---|
| `test_acceptance_v5.py` | the config fixture now **actually loads** — rebuilt by driving `load_lane_file` and reading its error one field at a time. `test_config_fixture_itself_loads_today` is the control for the controls: it uses the v4 spelling and is green NOW, so if it reddens every config test below it is failing for a fixture reason. Work items 6 and 6b each gain a message-based oracle, since the refusal itself already happens today. O5 asks pytest what it would **collect** instead of grepping for `--deselect`, closing the `-k` bypass |
| `sweep_v4_consumers.py` **v4** | a **location** rule (a file inside a frozen tree reading relative to itself — `carve-assets/P20/test_acceptance.py`, invisible to any text matcher) and a supplier guard on the environ branch, with module-constant resolution and the gate shell script included as a supplier |
| `verdict.schema.v5.json` | `helpers` gains `minItems: 1`, so A-230a's omission rule is enforced rather than merely stated — `helpers: []` previously validated |

**A-232 governs this and every later carve: a stated count is not evidence.**
The carve report pastes real command output and classifies each pre-implementation
red as legitimate or illegitimate.
