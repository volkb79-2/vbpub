# Assay P23 JIT carve and pre-dispatch adversarial specification review

Date: 2026-08-10
Carver/reviewer: gpt-5.6-sol xhigh
Post-predecessor anchor: `9d30b25b96b8ffd8f952c02e8958b923bb8e1d13`
Reviewed P22 parent: `cf49ec85459570ab77f2d06988a663e8e4e35afc`
AUTHORING revision: `2026-08-08-r5`
Disposition: **READY after correction**
Decisions: A-188–A-196

## Result first

P23 is ready for a Sonnet xhigh implementation and a fresh Opus xhigh review at
the exact P22 merge above. It was **not** dispatchable in its provisional form.

The provisional text named the right product goal—reuse one effective command,
one committed source state, and one lane budget—but still made an implementer
invent the execution seam, deadline object, source/refusal lifecycle, terminal
precedence, R3 base/profile handling, scratch accounting, and live-vbpub
self-hosting disposition. “R0 first” also left noncanonical declarations such
as `R0,R3,R2` legal even though production executes claims in canonical order.
Those are exactly the kinds of gaps that let an intelligent implementation and
an intelligent review converge on the same plausible wrong assumption.

The corrected 2c packet is implementation-shaped. It freezes the extended
immutable `CommandPlan`, required-plan `execute_plan`, injected `LaneDeadline`,
direct-R0 versus higher-rigor state machine, namespace map, exact P22 call and
context order, mutation/canary signatures, per-unit repository/output checks,
terminal table, injectable scratch lifecycle, scratch formulas, and mechanical
BLOCKED triggers. Its skeleton implements the grammar/seams and leaves one
explicit orchestration TODO. Its locked suite is a witnessed controlled red:
**13 intended failures, 6 mechanical
passes**. The real P22 composition tracer passes both locally and inside
`tester-unified:local` under the validated background cgroup.

No Assay production code was implemented and P23 was not dispatched. P22's six
locked assets remain untouched. Main's landed source state was preserved.

## Exact review prompt used

AUTHORING's exact prompt was applied first to the provisional P23 handoff plus
landed P20–P22, then again to the corrected handoff and locked packet:

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

## 1. Blocking ambiguities

| provisional ambiguity | why it blocked | correction |
|---|---|---|
| P22 whole-topology refusal versus Assay self-hosting in live vbpub | vbpub deliberately commits three absolute symlinks, so “snapshot every lane” makes the registered R0 gate impossible; weakening/filtering P22 would invalidate its security contract | A-189: exact R0-only lanes remain direct by declared product policy; any R1/R2/R3 lane must use P22 and may not fall back |
| “one effective command plan” had no landed execution API | lower modules could call `execute_command(lane, ...)`, silently re-resolving lane argv/env and dropping appended/passthrough values | A-193: exact plan fields, required `execute_plan(plan,...)`, direct-only wrapper; higher rigor receives no lane/ambient command source |
| A-155 required working-directory identity but the landed `CommandPlan` lacked it | plan equality could be asserted while a nested unit ran from a different project identity | `project_prefix: PurePosixPath | None`; higher-rigor plan requires the exact non-None repo-relative prefix; `None` means repository identity genuinely does not yet exist, never invented `.` |
| “one deadline” had no type, sampling rule, or process/P22 seam | convenient code could pass `lane.budget_seconds` to every process or mix UTC and monotonic clocks | one `LaneDeadline.start`, one injected monotonic source, positive remainder immediately before every P22/process boundary; UTC clock remains evidence-only |
| R0 prerequisite did not define rigor order | `R0,R3,R2` remained legal while runner emits R2 before R3 | A-192: R0-led ordered subsequence of the canonical four; uncovered-line additionally requires R1 |
| preparation/baseline/mutant/canary failures lacked a claim propagation table | implementer had to choose whether completed claims vanish, later claims run, or cleanup changes the reason | explicit terminal/side-effect table; completed earlier claims remain, affected/unstarted later claims share the real payload-free cause, except R2's discovered deadline identities remain its bounded payload |
| the first corrected draft said a non-PASS baseline starts no R3 but did not define a v4-valid R3 claim | payload-free R3 `FAIL` is invalid because a judged R3 outcome requires `CanaryResult`; blindly propagating R0 would fail construction | R2 propagates baseline as before; R3 emits complete failed-control `CanaryResult` and `INCONCLUSIVE/CANARY_INCONCLUSIVE`; uncovered-line uses the same early stop when baseline R1 is not PASS; locked cases require zero extra unit |
| P22 cleanup repair was not carried into P23 lifecycle | a worker could outlive the seed or normal-exit leak `RuntimeError` could mask the real P22 error | every child closes in worker, futures join, executor closes, then seed; failure preserves first real `AssayError`; normal-exit leak check stays distinct |
| snapshot-local writes were called “disposable” without stating whether they invalidate evidence | consumer stays clean, but a command can modify/commit support state and have higher rigor judge a different tree | A-195: check dirt once and HEAD only when clean for every relevant unit; keep `DIRTY_TREE`/`HEAD_CHANGED` semantics |
| canary target/base/profile ownership was implicit | a second config read, project/repo double-prefix, control-profile reuse, or transform diff against the wrong base could all look reasonable | one normalized target prefix; source read once from seed; fresh reservation/profile per half; control uses declared base, transform uses seed as base against P22 child |
| copied pack cost was acknowledged but not translated into P23 lifecycle | either unsafe hardlinks or unbounded simultaneous children remained plausible | A-194 fixes total-I/O and peak-space formulas, jobs-bounded live contexts, conservative tree bound, and no racy free-space oracle |
| the first scratch formula omitted the persistent prepared seed and scratch create/outer-cleanup terminals | reported peak and total work were understated, and an OSError could escape after HEAD was known with no complete artifact rule | `(U+1)` total pack writes, seed+children peak, create failure all-claim GIT_FAILED, normal-result cleanup failure replaces the highest higher-rigor claim without masking an earlier raised AssayError |
| reachable snapshot-limit pair remained excluded from ordinary audit | P22 controller correctly reverted forbidden conformance edits, leaving a real terminal called unreachable | A-190 assigns P23: byte-copy P22 artifact and remove only the exact exclusion; schema/raw/merged owners stay forbidden |
| P22 found shared tree/blob OIDs and descriptor pressure | an integration cache could collapse multiple path/mode identities or reopen one descriptor per path | A-191/A-196: P23/P29/P30 are path/entry scoped; consume P22's repaired per-object close discipline |

No externally visible P23 choice remains open after A-188–A-196. The P22 minor
nit that `materialize()` checks seed closure at context entry, not method call,
is explicitly nonblocking: P23 never stores a returned context for later; it
enters every context immediately inside the still-live seed.

## 2. False-PASS attacks

| oracle | convenient passing-wrong implementation | locked discriminator |
|---|---|---|
| O1 plan identity | artifact records appended argv, but R2/R3 call `execute_command(lane)` and lose it; fixtures use no append/env | nonempty append + present/missing passthrough + forbidden ambient key; every normalized ledger must be byte-equal except cwd/timeout |
| O1 nested cwd identity | copy project and run at scratch root; fixtures have project==repo | `apps/p` process reads `../../shared/input`; ledger also asserts exact `apps/p` suffix and plan prefix |
| O2 committed source | `copytree(project_root)` or copy whole working tree; ordinary fixture has no sibling/ignored state | tracked sibling + stale ignored coverage/cache; naive path demonstrably misses/copies both, P22 path sees/omits correctly |
| O2 fresh unit | reuse baseline snapshot/profile for mutants/control/transform | baseline/control write coverage while transform emits none; R3 must report wrong-cause `CANARY_SURVIVED`, never consume stale PASS-shaped bytes |
| O2 consumer isolation | mutate/restore source or rely on snapshot disposability without Git checks | source HEAD/tree/bytes/status before/after plus snapshot-local tracked support write that must become `DIRTY_TREE` |
| O3 declaration grammar | check only `"R0" in rigor` or sort declarations after load | literal load cases for `R2`, `R0,R3,R2`, uncovered-line without R1, and positive `R0,R2` |
| O4 mutant cap | build executor/futures then truncate to max; result count still looks bounded | max+1 direct boundary uses executor, prepared seed, and process objects that explode on first touch; exact sentinel still required |
| O4 deadline | give every process 100 seconds or notice expiry only after launching next | injected clock flips only after first mutant; ledger must contain baseline+one mutant, with second identity budget-stopped and no second process |
| O5 self-host policy | catch P22's unsafe-symlink error and retry direct, keeping gate green | same absolute-symlink repository: exact R0 direct PASS, higher-rigor payload-free GIT_FAILED with zero process calls |
| O5 terminal audit | validate only carver JSON or compare a generated fixture to itself | ordinary fixture must be byte-identical, exclusion absent, and independent Schema/direct-raw/merged layers all green |

The central adversarial lesson is that a flat clean repository, empty append,
empty environment, one mutant, one clock reading, and always-written coverage
makes almost every prior wrong construction green. The packet deliberately
combines axes rather than testing each in isolation.

## 3. Missing implementation-packet content now supplied

1. Exact extended `CommandPlan`, `execute_plan`, `LaneDeadline`, and
   `run_lane(..., monotonic=..., scratch_root_factory=...)` shapes in a
   compiling skeleton.
2. Exact direct-R0/higher-rigor state transition with no fallback.
3. Repo/project/prefix/diff/canary/artifact/cwd namespace table and the sole
   conversions.
4. Exact preparation, baseline, R1 target-read, R2 replacement, and R3
   control/transform call order against landed P22 signatures.
5. Explicit mutation and canary integration signatures so the implementer
   does not invent an adapter seam.
6. Rigor grammar and positive/negative TOML examples.
7. Deadline sampling, expiry, no-next-launch, and R2 partial-evidence rules.
8. Complete terminal/side-effect/claim propagation table.
9. Total pack-I/O, peak pack, conservative materialized-tree, concurrency, and
   cleanup formulas.
10. Carver-owned fixture manifest, byte process ledger, v4 artifact, skeleton,
    19-case acceptance suite, and real P22 tracer.

The handoff intentionally does not prescribe private helper names, whether the
runner uses one internal dataclass for unit results, or the exact executor
scheduling loop. Those choices cannot change a frozen observable. It does
prescribe every lower-layer input and output that could.

## 4. Scope and dependency defects

- `isolation.py` is now explicitly forbidden. P23 consumes P22 and cannot
  “simplify” a refusal, identity, or cleanup rule to make integration easy.
- `errors.py`, verdict/schema/verifier, and adapters remain forbidden. P21's v4
  already represents every P23 terminal; P23 is a producer/integration package.
- `tests/**` is allowed because extending `CommandPlan` mechanically changes
  direct constructors outside the initially enumerated focused files. Keeping a
  narrow incomplete list would force either forbidden stale tests or an
  unreviewed compatibility default.
- The implementation LOG is an explicit prospective touch path, and both P22
  and P23 carver-owned asset directories are mechanically forbidden. The body
  no longer asks for evidence in a path the frontmatter cannot authorize.
- The one ordinary snapshot-limit fixture and conformance edit are explicitly
  test-only. They close A-190 without touching model/schema/raw owners.
- `cli.py` remains forbidden. Its pre-adapter refusal may produce a plan with
  `project_prefix=None`, because repository identity has not entered that API;
  P23's actual higher-rigor `run_lane` plan must be non-None. This is an honest
  absence, not a shadow default.
- P29 was amended now for A-196: identical Go source at two paths remains two
  path-keyed target batches. P30 later owns independent execution.
- No schema v5 or later version is introduced. V4 remains the single current
  artifact schema.

Every P23 oracle is satisfiable inside corrected `scope.touch`. A need to edit
any forbidden owner is a mechanical BLOCKED route, not implementer discretion.

## 5. Corrected oracle and fixture matrix

### Requirement-to-oracle traceability

| requirement | owner | oracle | independent observable | controlled break |
|---|---|---|---|---|
| capture/reuse exact command once | runner plan/execute seam | O1 | object/byte ledger across all units | re-resolve lane/ambient in repeated unit |
| preserve nested committed topology | runner + landed P22 | O2 | sibling bytes, cwd suffix, stale absence, consumer hash | project copy/live copy |
| canonical declaration | config loader | O3 | real TOML positive/negative load | membership-only/order-sorting check |
| one deadline | runner + lower required argument | O4 | decreasing positive process timeouts and injected no-next-unit | reset per process or post-launch expiry |
| true max sentinel | P21 collector + mutation orchestration | O4 | exact max+1 payload with exploding later boundaries | truncate/submit first |
| independent replacement workers | mutation + P22 | O1/O2/O5 | per-path whole-blob child, ordered outcomes, closed lifecycle | source copy, hardlink, shared child |
| independent cause-sensitive halves | canary + unit runner | O1/O2/O4 | separate reservations/profiles and exact observed cause | control profile reuse or second config read |
| preserve P22 terminal | runner lifecycle | O5 | higher unsafe-symlink refusal, zero fallback process | catch and direct retry or cleanup masking |
| complete failed-control R3 | runner + existing canary model | O5 | R0/R1 failure creates valid inconclusive payload and starts no canary unit | payload-free judged R3 or rerun a broken control |
| singular scratch ownership | runner context boundary | O5 | injected entry/exit failures produce exact complete claims | uncaught OSError, nested temp root, or erased lower claim |
| close reachable v4 audit | ordinary tests | O5 | byte equality + three independent validators | generated expected or retained exclusion |

### Pairwise axes

| axis | values | paired witness |
|---|---|---|
| rigor route | exact R0; R0+R2; R0+R1+R3; invalid noncanonical | absolute symlink succeeds only on direct R0; same topology refuses higher rigor |
| repo/project | same; nested `apps/p`; tracked sibling outside project | nested ledger and real P22 tracer |
| command identity | declared; appended; passthrough present; passthrough absent; ambient forbidden | byte process ledger across baseline/replacement |
| source state | committed; ignored stale output/cache; unit-created tracked dirt; absolute symlink | stale absence, dirt terminal, refusal route |
| unit kind | baseline; mutant; canary control; transform | R2 ledger and R3 fresh-profile sequence |
| candidate bound | max-1; max; max+1 | Python real sites plus direct exploding-boundary sentinel |
| time | ample; expires after one mutant; P22 refusal | injected clock, ordered payload, zero next process |
| identity multiplicity | distinct bytes; identical blob at multiple paths/modes | P22 tests now; A-196 required in P29/P30 JIT fixtures |
| artifact | fresh; missing; stale consumer; snapshot-limit terminal | unit reservations and ordinary conformance closure |

### Mandatory combined-axis fixtures

1. **Nested plan/replacement:** `apps/p`, tracked sibling, nonempty append,
   present/missing passthrough, forbidden ambient env, ignored stale coverage,
   baseline plus one killed mutant; exact ledgers and consumer Git identity.
2. **Bound before execution:** four valid sites with max three and exploding
   executor/snapshot/process boundaries; exact max+1 sentinel.
3. **Deadline between queued units:** two sites, jobs one, injected expiry after
   first mutant process; second identity budget-stopped with no launch.
4. **Fresh-profile wrong cause:** consumer stale artifact, baseline/control
   write, transform omits; no unit starts with coverage and canary cannot PASS.
5. **Self-host split:** committed absolute symlink under the same product
   distribution; direct R0 executes once, higher rigor preserves P22 GIT_FAILED
   and executes zero times.
6. **Failed controls and scratch lifecycle:** R0 and uncovered-line R1
   failures start no extra canary unit but retain a constructible R3 payload;
   deterministic scratch entry/exit failures preserve the terminal table.
7. **Reviewer-owned attack:** must combine a different pair and state the
   convenient wrong implementation it made red.

## 6. Disposition

**READY.** The corrected P23 contract leaves no external interface, namespace,
clock source, default, order, bound, refusal, cleanup rule, or expected proof for
the implementer to invent. Sonnet xhigh is a suitable implementer now because
the remaining work is difficult integration under an explicit solution, not
product design. The skeleton removes mechanical grammar work and the locked
tests expose the highest-risk shared-assumption shapes. A lower model would
still be risky at 2c because four production modules and lifecycle/error
propagation must change coherently. Fresh Opus xhigh remains appropriate for
hostile review, especially concurrency cleanup, terminal precedence, and a new
combined-axis attack.

The 535-line handoff is intentionally larger than a terse goal brief. This is
the cost-saving choice the wave adopted: spend carver intelligence once so the
implementer checks against a solution-shaped contract instead of rediscovering
it in a 150k-token orientation and reviewer repair cycle.

## P22 successor dispositions

| disposition | P23 resolution |
|---|---|
| SB-P22-01 child-before-seed / preserve real failure | A-195 plus exact lifecycle and terminal table |
| SB-P22-02 O(units × pack bytes), no hardlinks | A-194 total/peak formulas and handoff §6 |
| SB-P22-03 identity override versus closed environment | A-188, cited as distinct controls |
| SB-P22-04 one canary target conversion | fixed project→repo prefix once; no config reread |
| SB-P22-05/SB-P22-R1 full refusal/live vbpub | A-189 direct-R0 boundary; no higher fallback |
| SB-P22-06/SB-P21-R2 snapshot-limit conformance owner | A-190 and locked byte-identical ordinary-fixture test |
| SB-P22-R2 OID/path multiplicity | A-191 for P23, A-196 promoted into P29/P30 |
| SB-P22-R3 descriptor bound | consume landed per-object discipline; no P23 OID/path descriptor cache |
| minor closure check at context entry | nonblocking; P23 enters every returned context immediately |

## Witnessed evidence

### Landed premise

- Main anchor: `9d30b25b96b8ffd8f952c02e8958b923bb8e1d13`.
- P22 reviewed parent: `cf49ec85459570ab77f2d06988a663e8e4e35afc`.
- Controller receipt: post-merge locked P22 acceptance `20 passed`; registered
  gate outer 0 with all phase/completion markers; raw log SHA-256
  `ca9539fafe071023fe1cfddaef15c54425cfabf78b8fb0b30441a80af9ffa93e`.
- The six P22 locked hashes recorded in the controller packet remained
  byte-identical; P23 does not touch their directory.

### Skeleton controlled red

The skeleton applies cleanly at the exact anchor and compiles. With only the
skeleton applied:

```text
.....FFFFFFFFFFFFF. [100%]
13 failed, 6 passed in 1.14s
```

The six passes are plan/deadline grammar, all three invalid rigor cases, one
valid ordered lane, and locked-input hashes. The thirteen failures are exactly the
unimplemented integration obligations: nested plan/replacement; new mutation
signature at max−1/max/max+1; injected expiry; snapshot dirt; R0/higher refusal
split; canary profile independence; failed R0/R1 canary-control construction;
scratch entry/exit terminals; and ordinary snapshot-limit closure.

### Real P22 composition tracer

The tracer passed locally and in `tester-unified:local` with `--network=none`,
a read-only repository bind derived from Docker inspection, and
`--cgroup-parent=dev-background.slice` only after
`assay/tools/cgroup-parent.sh` validated the configured host tier:

```json
{"base_and_child_see_tracked_sibling":true,"base_exact":true,"base_starts_without_ignored_coverage":true,"base_unchanged_while_child_live":true,"child_parent_is_source_commit":true,"commit":"23bd82427234087514b4eeb1a3062918112c6786","consumer_tree_unchanged":true,"naive_project_copy_contains_stale_coverage":true,"naive_project_copy_missing_sibling":true,"replacement_exact":true,"scratch_empty_after_contexts":true,"seed_read_exact":true,"status":"PASS"}
```

This proves the P23 construction is possible through landed P22 and that the
old project-copy shape fails on both topology and stale state. It does not claim
P23 production is green.

### Locked P23 asset hashes

```text
bd2e1485727816cd4f57045cfb32c9774cbe5f87c49a81624c7031e666096b5f  README.md
1e2cb8bd86139751b8f61818d474e42b013a736e20dad9cb02f842245ba1a3f7  skeleton.patch
708872ad2d9cfb4b0de27a1943e2a37ff6183e1cfeef520abc480bad631dd776  test_acceptance.py
690ab12b813d14792e6a89d0034c52e6100514317bc41fb8793737faf7c32591  fixture-manifest.json
0ace77ff3dbfc501717fc7d5a90ffec0fbffda02c1b9a7f675e061527e35139d  probe_reexecution_contract.py
5054c66eb4c76033fff7bc0ef019331e7ab88aa28944c025772664dd5199a566  expected/process-ledger.json
5b7f3cfc039b01c6d68e2169575b4580f3fd141cefa468fbc19f1088c91056a2  expected/r0-snapshot-limit-v4.json
```

The last hash is byte-identical to P22's locked expected document. The fixture
manifest records every asset hash except itself, avoiding a self-referential
hash.

## Post-P23 carver correction — SB-P23-01

After merge, the controller and both Opus review phases correctly retained two
red max-boundary parametrizations because the locked `FourSiteAdapter` was not
a conforming `LanguageAdapter` and assigned line numbers by discovery order.
The P24 Sol route repaired the carver-owned fixture exactly as prescribed:
added the three target-selection members and derived every `lineno` through
P21's `line_for_offset`. No production code or assertion changed. The exact
post-correction locked run is **19 passed**, and the two updated hashes above
supersede the original freeze values. Decision A-197 records the correction.
