# Assay P21 JIT carve and pre-dispatch adversarial specification review

> **Date:** 2026-08-09  
> **Frozen input/JIT anchor:**
> `618b6f15451ec5f45b5900dc496d794241180467`  
> **Handoff:**
> `nyxloom-trove/handoffs/assay-P21-verdict-v4-evidence-contract.md`  
> **Carver:** Sol xhigh  
> **Disposition:** **READY for Opus xhigh implementation**, followed by a
> fresh Opus xhigh independent review  
> **Canonical method:** `nyxloom/reference/AUTHORING.md`, exact
> “Pre-dispatch adversarial handoff review” prompt

## Result first

P21 is READY, but the provisional handoff was not. Applying the exact hostile
review against landed P20 found four defects capable of producing a false sense
of completion and five unspecified construction seams:

1. P21 promised `max_mutants+1` discovery while the only adapter API returned
   the complete tuple of full mutated source files. The cap could stop commands
   but not the unbounded memory/work it claimed to bound; its owning adapter
   files were forbidden until P23.
2. The proposed fallback—derive a “unique minimal changed byte span” from old
   and mutated full text—does not recover syntax-site identity. `<` to `<=`
   becomes a zero-width insertion, and `True` to `False` becomes `Tru` to
   `Fals` because the strings share a suffix.
3. “Reserve a sibling temp before the command” left an observable file inside
   the consumer namespace. If the verdict target is in the repository, that
   temp can make P20's dirt guard refuse the lane; path namespace, destination
   replacement races, prior-artifact preservation, and stdout ownership were
   also unspecified.
4. “Model/schema/raw verifier reject the same facts” credited Draft 2020-12
   with cross-object arithmetic, target equality, and timestamp ordering it
   cannot express. That would let a schema-green test stand in for a check that
   never existed.
5. `candidate_count` did not say whether it was an exact unbounded total or a
   censored bounded observation at the sentinel.
6. The prospective max+1 shape had no exact model-versus-claim ownership, so a
   model could accept it under PASS or reject the valid budget terminal.
7. `exclusion_capability` named two values but not how a mixed parsed profile
   was handled; “infer from format” or “empty means unavailable” were both
   plausible wrong implementations.
8. HEAD_CHANGED said both “resolve HEAD immediately after” and “dirt first,”
   leaving duplicate observations and combined commit-plus-dirt precedence to
   the implementer.
9. The opening claim said every artifact fact was bounded even though unchanged
   argv/env/description strings remain outside this package's declared bounds.
   The claim is now precise: complete checkable evidence plus bounded mutation
   cardinality.

The freeze resolves these rather than routing them into Opus as “private
choices.” P21 now owns the already-designed bounded common/Python
`MutationSite` seam (A-180); P23 consumes it. The output boundary has one
descriptor-owned state machine with no persistent pre-run temp (A-181). V4 has
explicit validation-layer ownership and exact arithmetic/path/time rules
(A-182). The packet carries three complete handwritten v4 artifacts, fourteen
deterministic invalid complete documents, a UTF-8 site manifest, a compiling
output skeleton, and 24 locked acceptance cases. All 24 are controlled red on
the exact anchor with no collection/setup failure.

## Exact review prompt used

The following AUTHORING prompt was applied verbatim to P21 and every source
named by its context section:

> Review this handoff as a hostile implementer, a hostile environment, and an
> independent acceptance engineer. Do not propose code yet. Build a
> requirement-to-oracle traceability table and try to make every oracle pass
> while violating the stated product goal. Identify: undefined interfaces or
> data grammar; values the implementer must invent; shadowing or silent
> defaults; ambiguous ownership; missing terminal states; repo/project,
> host/container, source/artifact, or declared/effective namespace confusion;
> stale or producer-authored evidence; unbounded work; order, clock, ambient
> environment, and repeated-execution dependence; scope/dependency conflicts;
> and tests that share the implementation's assumption. Then construct a
> pairwise input matrix and name at least three combined-axis fixtures likely
> to break a convenient implementation. For each oracle, give one plausible
> wrong implementation that still passes the proposed test. Mark the handoff
> NOT READY if any externally visible decision, interface, example, bound,
> refusal, or proof source remains for the implementer to invent. Return only:
> (1) blocking ambiguities, (2) false-PASS attacks, (3) missing implementation-
> packet content, (4) scope/dependency defects, (5) a corrected oracle/fixture
> matrix, and (6) READY or NOT READY with reasons.

The required six-part result follows.

## 1. Blocking ambiguities

All blocking ambiguities found by the review are resolved in the handoff:

| ambiguity | hostile consequence | frozen correction |
|---|---|---|
| cap runs after `generate_mutants` | commands are capped but memory already holds N full files | P21 replaces the seam with selected-operator, remaining-capacity `MutationSite`; Python retains at most the limit and collection stops at max+1 |
| site identity derived from full-text diff | insertion/shared-suffix identities do not name the syntax token | site supplies exact UTF-8 byte span and replacement; wire hash is derived from replacement bytes only |
| site order/uniqueness omitted | completion order or descriptions become identity | exact key `(path,start,end,replacement_sha256,operator)`; line/description diagnostic; uniqueness across all buckets |
| invalid syntax versus zero sites | inability to parse can become honest “nothing mutable” | invalid source raises `MutationDiscoveryError`; valid zero is `NO_MUTANTS` |
| candidate count meaning | a total may require unbounded discovery, or a sentinel may lie about exact total | bounded observed count; max+1 means “at least max+1,” and discovery intentionally stops |
| max+1 cross-field owner | sentinel can appear under PASS or with wrong max | local `Mutation` admits only normal/prospective-sentinel shapes; Verdict/Claim and raw verifier bind exact reason and `judgment.r2.max_mutants+1` |
| output target namespace | relative file can land under project root or caller cwd depending on implementer | CLI-process cwd/root namespace fixed; never project-relative |
| output preflight construction | persistent temp changes repository dirt; `os.access` races | create/remove exclusive sibling probe, hold parent + observed destination identity, no temp across execution |
| target changes after preflight | late writer is overwritten by atomic replace | revalidate identity; preserve appeared/changed object; typed output error |
| stdout ownership | preflight closes/tests real stdout inconsistently | zero-length writability probe, emit complete text once, never close caller stream |
| schema parity slogan | nonexistent cross-field Schema checks are credited as evidence | schema owns local grammar; model and differently-worded raw checks own temporal/cross-object relations |
| timestamp comparison | lexicographic strings reject valid offset pairs | parse aware instants; canonical valid example is intentionally lexically reversed |
| exclusion capability source | known-empty and unavailable can collapse again | unanimous `FileCoverage.excluded is None` derivation; mixed profile unreadable |
| HEAD_CHANGED observation order | combined commit+dirt can depend on `or` order or a second status/head read | one post-command dirt call; dirty terminal immediately; one post-HEAD call only on clean branch |
| old-version diagnostic order | a sparse old artifact gets current required-field noise before version | version check immediately after top-level object check; exactly one version diagnostic |

No external P21 interface, serialized form, path grammar, state transition,
bound, refusal, or proof source remains for the implementer to invent.

## 2. False-PASS attacks

| oracle | plausible wrong implementation that passes convenient tests | locked/required attack |
|---|---|---|
| O1 | close only payload membership; change policy and payload together to a new string | `mutation-agreeing-unknown-operator` changes both and must fail model, Schema, and raw verification |
| O1 | bump schema and let raw verifier return model reconstruction only | each raw cross-field check is differently worded and mutated independently; old versions return one early diagnostic |
| O2 | generate every full mutated file, slice to max+1, then claim bounded discovery | UTF-8 site manifest plus fake max+1 adapter; executor is forbidden and no full-text candidate API remains |
| O2 | find a minimal string diff to populate span/hash | manifest pins whole `<` and `True` token spans, defeating zero-width/shared-suffix collapse |
| O2 | keep killed as a count while adding identities only to failures | complete combined artifact and `mutation-killed-is-count` require a killed identity array |
| O3 | infer unavailable whenever excluded detail is empty | two complete valid R1 artifacts distinguish reported-empty from unavailable-empty; unavailable-with-detail fails |
| O3 | check only canary mechanism or change both targets to `src/../p.py` | target mismatch and agreeing-nonnormal target are separate complete documents |
| O4 | compare timestamp strings | valid offset-crossing canonical object plus actually reversed instant |
| O4 | call `os.access`, or hold a visible temp throughout the run | missing-parent CLI marker proves zero consumer work; appeared-object race proves no overwrite/temp residue |
| O5 | retain P20's `dirty or head_changed -> DIRTY_TREE` | real clean command commit requires HEAD_CHANGED; commit then leftover dirt requires DIRTY_TREE |

The fresh reviewer must add at least one undisclosed combined-axis attack. The
locked packet is a floor, not a replacement for independent distribution shift.

## 3. Missing implementation-packet content

The JIT freeze added:

- exact ordered mutation vocabulary and its owner;
- exact `MutationSite`, `MutationDiscoveryError`, adapter, collection, job, and
  `run_mutation(max_mutants=...)` signatures;
- byte-boundary, replacement, line, operator, limit, order, uniqueness, and
  invalid-source rules;
- bounded Python selection and cross-file stop pseudocode;
- deletion of the old full-text compatibility surface;
- exact `MutantOutcome`, normal/sentinel arithmetic, bucket uniqueness,
  policy membership, and claim/reason correspondence;
- exact coverage-capability provenance and mixed-profile refusal;
- normalized project/repository wire path grammar;
- parsed timestamp ownership and the offset-crossing positive witness;
- exact output namespace, state machine, identity checks, side-effect order,
  error translation, CLI precedence, and no-fallback behavior;
- exact HEAD_CHANGED/dirty observation order;
- three complete valid artifacts, fourteen complete invalid documents defined
  by base plus exact pointer replacements, and the layer each can express;
- a compiling output skeleton, handwritten UTF-8 candidate manifest, locked
  acceptance command, traceability, and explicit degrees of freedom.

## 4. Scope and dependency defects

- The provisional P21 forbade `adapters/python.py` while requiring a bound its
  return contract made impossible. P21 now touches `base.py`/`python.py`; the Go
  adapter remains forbidden and unregistered for R2.
- P23 formerly duplicated ownership of the common site seam. Its frontmatter,
  packet, work, and forbids now consume P21's landed interface and prohibit all
  adapter edits.
- P29's stale references to “P23's MutationSite” now name P21; it still owns
  only the Go implementation/helper.
- The P21 output owner is a new explicit `src/assay/output.py`; `safeio.py`
  remains P20's project-relative producer-artifact boundary and is not
  overloaded with arbitrary CLI-process output namespaces.
- Every locked P21 asset is named individually in frontmatter `scope.forbid`,
  because nyxloom lint does not treat a recursive wildcard as an existing path.
- P22 remains the next JIT package and owns snapshot mechanics. P21 reserves
  its reason only; it does not create or redesign isolation.

There is no remaining touch/forbid contradiction. `nyxloom lint` reports only
the intentional L10 size warning: this class-2b package is large because the
solution and proof are transferred up front to avoid implementer inference.

## 5. Corrected oracle and fixture matrix

### Requirement-to-oracle traceability

| requirement | owner | oracle | independent observable | controlled break |
|---|---|---|---|---|
| current-only v4 and closed vocabulary | vocabulary/model/schema/raw verifier | O1 | three valid docs; fourteen named invalid docs; one old-version diagnostic | agreeing policy+payload unknown operator; v3 complete artifact |
| killed and all attempted identities | verdict/mutation/verify | O2 | combined artifact names killed site; identity arrays exact/sorted/unique | killed integer; reversed span; uppercase hash; duplicate/cross-bucket identity |
| bounded discovery and cap | base/python/mutation/config | O2 | UTF-8 manifest at limit 4 and 3; max+1 sentinel; forbidden executor | full-text tuple, append-all then slice, missing/0/10001 max |
| canary target witness | config/canary/verdict/verify | O3 | payload target exactly equals policy | change one target; change both to nonnormal spelling |
| exclusion capability | coverage/evaluate/verdict/verify | O3 | reported-empty and unavailable-empty both valid and distinct | infer from empty detail; unavailable plus detail; mixed profile |
| temporal interval | verdict/verify | O4 | valid lexically reversed offset pair | actual end instant before start |
| output readiness/atomicity | output/cli/runner | O4 | missing-parent marker absent; appeared object preserved; no temp | run then write; `os.access`; persistent pre-run temp; path-only replace |
| commit identity terminal | runner/git | O5 | real clean commit HEAD_CHANGED; commit+dirt DIRTY_TREE | shared `or` terminal; check HEAD before dirt; start R1+ |

### Pairwise matrix

| axis A | axis B | required observation |
|---|---|---|
| payload operator | policy operator | changing both to unknown still rejected by closed vocabulary |
| same line/description | distinct byte spans | identities remain distinct and stable |
| UTF-8 prefix | byte offset | manifest offsets address actual encoded token bytes |
| insertion/shared suffix | syntax token | identity remains whole site, not minimal text diff |
| max_mutants | candidates | max-1/max normal; max+1 sentinel and zero submissions |
| earlier target file | later target file | collection passes remaining capacity and never calls later file after sentinel |
| reported/unavailable | empty/populated exclusions | reported-empty and unavailable-empty distinct; unavailable-populated refused |
| canary policy | payload target | exact normalized equality in model and raw verifier |
| started offset | ended offset | parsed instants decide, not lexical order |
| pre-existing target | post-reservation replacement | unchanged regular may be atomically replaced; changed/appeared object preserved |
| relative output | nested project root | output stays relative to CLI cwd, not consumer project |
| clean moved HEAD | post-command dirt | clean gets HEAD_CHANGED; any dirt gets DIRTY_TREE |

### Mandatory combined-axis fixtures

1. policy and killed payload both changed to the same unknown operator, with
   otherwise valid identity/arithmetic;
2. non-ASCII source prefix + two same-description comparisons on one line +
   insertion and shared-suffix replacements + limit truncation;
3. max+1 sites across ordered files + jobs greater than one + executor that
   fails if constructed;
4. unavailable capability + nonempty excluded mapping and matching summary;
5. policy and payload both changed to the same nonnormal canary target;
6. existing output reservation + object appears after reservation + no
   fallback/temp residue;
7. command moves HEAD and then either leaves clean or creates unrelated dirt,
   with higher-rigor callbacks forbidden;
8. offset-crossing valid timestamps beside an actually reversed instant.

## 6. Disposition

**READY.** Reasons:

- all externally visible choices are fixed;
- the output skeleton applies and compiles at the exact anchor;
- all 24 locked acceptance cases are controlled red at the anchor for named
  absent v4/site/output/HEAD behavior, with no collection/setup failure;
- complete valid and invalid inputs are carver-authored and forbidden to the
  implementer;
- the old full-text cap contradiction is removed before it can ship;
- P23/P29 and project decisions/design/state now agree with the moved owner;
- handoff lint has no error;
- P20's authoritative gate receipt binds the landed input branch to all phase
  markers and merge `618b6f15`; this carve changes no product code.

Route to **Opus xhigh**, not Sonnet: after carving, public behavior and proof are
fixed (class 2b), but the atomic v4 migration plus bounded AST selection and
independent raw verification still require difficult private construction. A
fresh Opus xhigh context reviews it; it must not reuse the implementer's model
session.

## Witnessed evidence

### Premise and inherited gate receipt

At review start, main was clean at
`618b6f15451ec5f45b5900dc496d794241180467`. The P20 controller receipt binds
its reviewed feature commit `ff7b09e70cba66ab289e57d3b5fe1883cb8a9f91`
to merge `618b6f15`, locked acceptance `15 passed`, outer exit 0, all three
inner phase markers, final `ASSAY_REGISTERED_GATE_COMPLETE=1`, and gate-log
SHA-256:

```text
ef5738012957619cc8ff6324119005704ca6eec980ebef8423e6f8ca4880c11c
```

No product code changes in this carve, so rerunning that expensive installed-
wheel gate before implementation would produce no new product evidence. The
controller will run it once on the final reviewed P21 commit.

### Skeleton and controlled-red witness

In a disposable detached worktree at the exact anchor:

```text
git apply .../carve-assets/P21/skeleton.patch       PASS
python -m py_compile assay/src/assay/output.py      PASS
locked pytest collection                            24 tests
locked pytest result                                24 failed in 0.85s
```

Observed failure classes were the intended current defects: 18 v4 contract
cases stopped at current `VERDICT_SCHEMA_VERSION == 3`; Python has no bounded
site method/type; config accepts missing `max_mutants`; HEAD_CHANGED is absent;
the old CLI executed before an invalid output failed with bare
`FileNotFoundError`; and the skeleton's reservation TODO remained red. There
was no import, fixture, collection, timeout, or ambient setup failure. The
temporary worktree was then removed; no product file or Git worktree remained.

The v4 assertion intentionally gates each invalid-document test: at the anchor
the migration itself is absent, while after the implementer bumps the version
each case proceeds to its own layer-specific negative. This prevents the v3
schema's blanket rejection of every v4 document from being miscounted as proof
that v4 rejects the particular malformed field.

### Locked asset hashes

```text
f90b88a37a4f1ac69f0e4fcef7643f09e88cb9ff7c1a6dbef9ec1e7480268256  README.md
8ef705b0485a4f3c4e06484c32262237293e00ae6999d99269e23056e7dd9de9  skeleton.patch
996793659253cbec59939bc8567a4fa41121565603dac131db99915d68c78d9d  test_acceptance.py
24657889af5ba504b337ebd8aacff56b21a0070d12f1da896f25fb719035c6b8  python-site-manifest.json
5798883ce02dafec74abd9cff587c869608f2720993920b7379b0d8569842506  invalid-cases.json
e01b40c5412eff66a881dc27356d0ff55d302acdff3aa7d04ee579ed4778d054  expected/combined-pass-v4.json
c671da8e4f0e50c1372d014903491040295f4fab8a4635a300e5fd41607d5957  expected/r1-unavailable-v4.json
41439fe0e78a542cfbbe495e8a1d37c4fa011e324566615d3312f2f6f60b9622  expected/r2-limit-v4.json
```

### Mechanical controller action

Create exactly one fresh worktree and branch from the committed P21 JIT-freeze
OID, apply the locked skeleton, dispatch one fresh **Opus xhigh implementer**
with the frozen-orientation diff instructions and one-hop brief, run the locked
suite, then dispatch one fresh **Opus xhigh reviewer**. Only the Luna controller
runs/hashes the registered gate and merges after both locked and ordinary proof
are green. No product decision or mechanical BLOCKED trigger remains.
