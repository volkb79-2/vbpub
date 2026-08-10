# Assay P22 JIT carve and pre-dispatch adversarial specification review

Date: 2026-08-09
Carver/reviewer: gpt-5.6-sol xhigh
Post-predecessor anchor: `678104ad32f26b9fbccdbb38b3298149a1d8f8e0`
AUTHORING revision: `2026-08-08-r5`
Disposition: **READY after correction**
Decisions: A-184–A-187

## Result first

P22 is ready for an Opus xhigh implementation and a fresh Opus xhigh review at
the exact P21 merge above. It was **not** ready in its provisional form.

The old packet specified a stateless `materialize_snapshot(spec)` call while
requiring both full reachable history and a fresh snapshot per repeated unit.
That left two convenient implementations: re-enumerate/re-pack a large history
for every mutant, or share the source/seed through alternates or hardlinks. The
first makes cost multiply; the second breaks isolation. The live vbpub closure
at the anchor is 26,074 objects and 273,578,621 uncompressed bytes, despite a
small ~22 MiB existing pack. This was a product-interface gap, not a private
optimization choice.

The corrected solution prepares and validates one unexposed private seed, then
serves concurrent independent base/replacement repository contexts from it.
It also freezes the missing scratch and time-budget inputs, entry/path-total
bounds, source alternates/partial/shallow refusal, raw tree grammar, `.git`
collision rule, clean index/HEAD construction, repo-relative whole-blob
replacement, fixed child identity, terminal mapping, and P21's reserved
snapshot-limit complete artifact. P23 was updated to consume the seed exactly
once per lane and pass its current remaining seconds to every P22 call.

No production code was implemented. The carver-owned skeleton compiles and the
locked suite is a witnessed controlled red. The independent Git tracer passes
inside `tester-unified` under the repository-mandated validated background
cgroup. Its strongest unexpected result is useful: the tempting real
`git archive` construction executed the committed hostile filter and still
produced no private `.git`; raw object transfer executed nothing.

## Exact review prompt used

The following AUTHORING prompt was applied to the provisional P22 handoff, its
named context, the actual P20/P21 code at the anchor, and then again to the
corrected packet:

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

| provisional ambiguity | why it blocked dispatch | correction |
|---|---|---|
| Stateless `materialize_snapshot(spec)` plus full history and one snapshot per repeated unit | Implementer must choose repeated full transfer versus unsafe sharing | A-184: `prepare_snapshot` transfers once; concurrent `SnapshotRepository` methods produce independent contexts |
| No scratch-root input | `tempfile` environment, consumer tree, or an invented default could own security/cleanup | `SnapshotSpec.scratch_root` is required, resolved, real, outside repo, caller-owned; P22 removes only unique children |
| No time-budget seam | P23 promises one lane deadline but cannot cancel/limit P22's Git/copy work after it lands | Every P22 call requires positive finite `timeout=remaining`; one internal monotonic deadline, no reset/default; expiry is `LANE_TIMEOUT` |
| Object count but no entry or total-path count | One blob can be referenced by arbitrarily many paths | Added `max_entries=200,000` and `max_total_path_bytes=64 MiB`, alongside fixed path/blob/object/pack bounds |
| “No alternate” described only for the destination | A source-local `objects/info/alternates`, partial clone, shallow file, or graft still changes the byte source | A-185 refuses local alternates/grafts/shallow/partial/promisor/non-SHA-1 state and disables lazy fetch |
| Existing P20 API buffers one command's stdout at 64 MiB, while provisional pack limit was 512 MiB | No defined construction can stream the promised pack through the only Git owner | `git.py` gains private bounded input/stream supervision; exact OID list feeds `pack-objects` directly to private `index-pack` |
| `ls-tree` prose did not define hostile raw tree grammar | Malformed mode, duplicate, `.git`, UTF-8, newline, backslash, and prefix rules were invented by implementation | Raw SHA-1 tree record grammar, iterative traversal, allowed mode table, path byte rules, and terminal are fixed |
| “Private repository” omitted index construction | A tree can have exact files and HEAD but still be dirty because the index is absent/wrong | Non-checkout private `read-tree`, detached HEAD, then exact HEAD/status/bytes/modes/closure verification before yield |
| Replacement `path` and `expected` were unnamed namespaces/shapes | Project-relative and repo-relative are both plausible; `expected` might mean a span or a whole blob | Repo-top-relative normalized path; `expected`/`replacement` are complete blob bytes; P23 builds the full splice inside its worker |
| No stable terminal for unsafe tree/I/O or stale replacement | Implementer had to invent a reason or leak a traceback after HEAD is known | unsafe/operational Git state → `ERROR/GIT_FAILED`; fixed limit → `SNAPSHOT_LIMIT_EXCEEDED`; stale descriptor → `MUTATION_DISCOVERY_FAILED` |
| P21 reserved the snapshot terminal but P22 scope could not close its conformance exclusion | Correct implementation would leave the vocabulary audit calling a now-reachable pair unreachable | Scope adds the one conformance file/fixture; exact R0-only document is locked; schema/model/verifier remain forbidden |
| P21 successor brief warned that canary target is normalized, but P22 said “round-trip” without saying whether it rereads config | A second reader could compare a different spelling or force a forbidden config edit | P22 never reads lane/config; P23 prefixes P21's already-normalized target exactly once |

No blocking ambiguity remains after A-184–A-187. The L10 handoff-size warning is
accepted: this is a 2b security/performance substrate, and compressing the
packet would give the implementation back the decisions it was expanded to
remove.

## 2. False-PASS attacks

| oracle | plausible wrong implementation that passes a convenient test | corrected discriminator |
|---|---|---|
| O1 exact topology/one transfer | `copytree(project_root)` on fixtures where project==repo; or reopen source objects for every snapshot | nested `apps/p` reads tracked `shared/`; source `.git` is renamed away after preparation and base+replacement must still work |
| O1/O2 private object identity | write `objects/info/alternates` to the source or hardlink its/seed's pack; tests compare only file bytes | source/sibling object inode sets must be disjoint, alternates absent, simultaneous snapshots mutate independently |
| O2 inert committed bytes | use `git archive`/checkout and test only ordinary blobs | committed filter/hook/replace/core.worktree traps plus sentinel; tracer proves archive executes the filter while raw object path does not |
| O2 no ignored input | copy repository working tree with `symlinks=True`, which fixes dereference but still copies ignored profile/FIFO/socket/cache | literal manifest and absent list assert every ignored special/regular object is absent |
| O3 safe tree | bound unique objects only, use `Path.resolve()` to “validate” links, or accept whatever `ls-tree` prints | entry/path-total limit+1, raw `100664`, gitlink, absolute/escape links, and scratch-empty/non-yield assertions |
| O3 path fidelity | decode display lines or reject awkward legal POSIX paths to simplify parsing | one committed UTF-8 filename includes a newline and literal backslash; exact bytes/hash are hand-authored |
| O4 exact replacement | remove `project_prefix` from the path a second time, use ambient author/time, or invoke `git commit` with hooks disabled only in a happy repo | replacement targets tracked sibling outside `apps/p`, hostile ambient identity/config, exact parent/OID/status, same-input child equality |
| O4 independence | update the prepared seed then clone it, so later base snapshots contain the mutant | hold base and changed contexts concurrently, then create the same child again; base/source hashes and bytes remain fixed |
| v4 terminal | leave the exclusion because integration is P23, or make an R2 mutation-limit-shaped artifact | exact locked R0-only complete document, direct raw checker calls, Schema, and merged verifier all pass; conformance exclusion must be removed |

The main false-PASS theme is the same as P15–P19: a normal repository where
repo==project, no hostile local config, no ignored state, one small tree, and one
sequential snapshot makes nearly every wrong implementation look correct.

## 3. Missing implementation-packet content

The corrected handoff now supplies:

1. Exact `SnapshotLimits`, `SnapshotSpec`, `Snapshot`, `SnapshotRepository`,
   and `prepare_snapshot` signatures in a compiling skeleton.
2. Exact source/seed/snapshot/project/replacement/scratch namespace map.
3. Fixed default bounds and the source for every non-test value.
4. One-transfer construction, exact full-OID pack flow, and source-disconnect
   proof.
5. Raw tree wire grammar, supported modes, UTF-8/path/symlink rules, and fixed
   materialized modes/mtime.
6. Exact timeout ownership and `LANE_TIMEOUT` behavior without a default.
7. Full terminal/side-effect table and cleanup behavior.
8. Exact private-index and fixed `commit-tree` recipe, identity, timestamp,
   message, path namespace, and whole-blob equality rule.
9. Carver-authored literal manifest, complete v4 expected artifact, hostile
   fixture builder, linked-worktree/source-removal proof, concurrent inode
   attack, and seven independent limit axes.
10. A work-to-owner/oracle/fixture/controlled-break table and mandatory LOG
    failure counts.

The packet deliberately does not prescribe incidental helper names, select
versus poll implementation, raw-tree function decomposition, or copy versus a
verified distinct-inode reflink. Those cannot change visible behavior.

## 4. Scope and dependency defects

- The provisional P22 scope could not satisfy P21 reviewer disposition
  `SB-P21-R2`. It now permits only
  `tests/test_verdict_conformance.py` and the one exact new verdict fixture;
  schema, model, and verifier stay forbidden because all three already accept
  the R0-only document.
- `SB-P21-R1` is honored: when the locked test claims raw-layer evidence it
  calls the raw `_check_*` functions directly before calling the merged
  `verify_document`; the latter alone cannot prove which layer caught a defect.
- `SB-P21-02` is resolved without widening P22 into config. P22 never reads
  `assay.toml`; P23 consumes the normalized target landed by P21.
- P20 remains the sole Git subprocess owner. P22 may extend its private bounded
  process seam but cannot launch Git from `isolation.py` or weaken P20's exact
  executable/environment/repository identity.
- P23's provisional contract was stale against the new prepared-seed and
  required-timeout interface. It now prepares once, loads mutation target bytes
  from the seed, uses repo-relative whole-blob replacement, and passes remaining
  seconds before every call. Its own JIT assets still wait for P22 to land.
- No downstream package needs another schema version. P21's v4 remains the one
  current artifact contract.

Every P22 oracle is satisfiable inside the corrected `scope.touch`. No
forbidden file is secretly needed.

## 5. Corrected oracle and fixture matrix

### Requirement-to-oracle traceability

| requirement | owner | oracle | locked observable | controlled break |
|---|---|---|---|---|
| prepare full source closure once | `git.py` + `isolation.py` lifecycle | O1 | remove source `.git` after prepare; later base/replacement exact | reopen source/alternate |
| preserve repo/project topology | raw manifest/materializer | O1/O2 | `apps/p`, tracked `shared`, exact project root | copy project only |
| ignore consumer state/config/programs | P20/P22 Git boundary | O2 | replace/filter/hook/core.worktree + stale FIFO/socket/profile; sentinel absent | archive/checkout or inherit local config |
| independent private repositories | materializer | O2 | clean detached HEAD/index, no alternates, disjoint inode sets, concurrent mutation | hardlink/shared root |
| bounded raw tree/materialization | parser + limit owner | O3 | entry/path/blob/object/pack limit+1, malformed mode/gitlink/link | truncate or bound objects only |
| exact legal path/mode bytes | parser/materializer | O3 | hand SHA-256/mode/target manifest including newline/backslash | display-line parser or normalize path spelling |
| deterministic replacement | private plumbing | O4 | repo-relative sibling, exact bytes/parent/OID/status under hostile identity | project-relative path, ambient commit, or seed mutation |
| truthful snapshot terminal artifact | ordinary conformance tests | O3 | exact R0-only v4 fixture passes raw/Schema/model | retain exclusion or add mutation payload |
| one lane budget | P23 deadline + every P22 timeout | O3/O4 | invalid timeout locked; ordinary injected expiry required | unbounded/default timeout or reset per call |

### Pairwise input matrix

| axis | values frozen in packet | paired witness |
|---|---|---|
| repository topology | repo root; nested `apps/p`; tracked sibling | nested + sibling + repo-relative replacement |
| source namespace | normal `.git/`; linked-worktree `.git` gitfile; replace ref; local core.worktree; filter/hook; local alternate; shallow; partial/promisor | success hostile/linked fixtures; separate fail-closed topology matrix |
| source state | clean tracked; ignored regular; FIFO; socket; cache; source unavailable after prepare | hostile manifest; source-disconnect test |
| tree entry | 100644; 100755; contained symlink; absolute/escape symlink; gitlink; raw unsupported mode | literal manifest + structural refusal matrix |
| path bytes | ASCII; UTF-8; newline; backslash; `.git`; traversal; total limit+1 | odd-path manifest; raw/refusal and limit tests |
| resource bound | object; entry; per-path; total-path; blob; total-object; pack; remaining time | seven locked limit axes plus constructor/timeout cases |
| materialization | base; replacement; concurrent siblings; source disconnected | concurrent six-unit and source-removal tests |
| replacement | inside/outside project prefix; correct/stale expected; fixed/ambient identity; repeated same input | tracked sibling replacement with simultaneous base and hostile env |

### Mandatory combined-axis fixtures

1. **Nested hostile success:** `apps/p` + tracked `shared` + replace ref + local
   `core.worktree` + committed filter + hook + ignored coverage/FIFO/socket +
   contained symlink + executable + NUL blob + UTF-8/newline/backslash path.
   Exact hand manifest, clean HEAD/status, no sentinel, and source digest all
   agree.
2. **Prepared-source independence:** prepare the same nested repository, rename
   source `.git` away, then create both a base and repo-relative tracked-sibling
   child. This catches source reopening, alternates, project-relative
   reinterpretation, and stateless re-transfer together.
3. **Replacement/concurrency/ambient attack:** hold base and child contexts
   simultaneously, set hostile ambient author/time, assert distinct object
   inodes and unchanged source/base bytes, then reproduce the exact same child
   OID a second time.
4. **Hostile refusal distribution:** unsafe symlink or gitlink/raw unsupported
   mode with a tight resource ceiling and caller scratch; no seed/snapshot is
   yielded and scratch is empty. The reviewer must combine at least one pair
   differently rather than merely repeat these named cases.

## 6. Disposition

**READY.** On the corrected handoff:

- no externally visible type, namespace, grammar, bound, timeout, terminal,
  side-effect order, or proof source remains for the implementer to invent;
- the highest-risk pack/child construction has passed a real independent tracer
  inside the declared gate image;
- every unimplemented construction case is a witnessed red on the compiling
  skeleton, while skeleton-supplied validation and the independent complete-
  artifact test are already green;
- P21 successor dispositions and P23's new dependency surface are durably
  incorporated; and
- the remaining difficulty is honestly 2b: complex private bounded streaming,
  tree parsing, concurrency-safe materialization, and cleanup under fixed public
  behavior. Opus xhigh is the correct implementation route. Sonnet xhigh would
  be an avoidable risk here; Luna is controller-only.

## Witnessed evidence

### Premise and landed predecessor

- `main` was clean at P21 merge
  `678104ad32f26b9fbccdbb38b3298149a1d8f8e0` before carving.
- Luna's controller receipt records P21 reviewed head `bbf5cc46`, registered
  gate exit 0, all four phase/completion markers, raw-log SHA-256
  `d79241d71837ff90ebce446288359367a10f5ec5aeaa193579c983b2180fed50`,
  and post-merge locked acceptance `28 passed`.
- P21 review dispositions were applied: normalized canary path is consumed,
  raw-checker evidence is direct, snapshot-limit exclusion moves to P22, and
  the missing-tool exclusion moves to P27. The unrelated Go item is promoted
  into P29 as the exact `MutationSite.identity` order plus a reversed-valid-
  batch refusal.
- Live full-closure probe at the anchor:
  `objects=26074 total=273578621 max_blob=11198464`; existing Git storage
  reports 28,787 packed objects / 21.58 MiB plus 291 loose / 2.04 MiB. This is
  the evidence behind the prepared-seed API rather than a guessed performance
  argument.

### Real-Git tracer

Run in `tester-unified:local` with the repository bind source derived from
Docker inspection, read-only, and
`--cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND"` after
`tools/cgroup-parent.sh` validated it:

```json
{"base":"b6909421dc0433facafd41c413f551d4ee64ef50","child":"e4a34d14dba7c3696abc5fd608e328e9f5761b02","consumer_program_executed_by_product_construction":false,"git_archive_executed_consumer_filter":true,"git_archive_has_private_repository":false,"naive_project_copy_contains_stale_ignored":true,"naive_project_copy_missing_sibling":true,"new_blob":"d3df623915b877690a266f822cc9d619fd45e8df","objects":12,"old_blob":"7ab80e00ffb35208ed0911245be6dd009c7fb99d","pack_bytes":720,"private_status":"clean","source_object_store_unchanged":true,"status":"PASS","total_uncompressed_bytes":637}
```

The same tracer returned the same base/child/object/blob/pack values in the
devcontainer before the gate-image run. The container is the authoritative
environment witness.

### Skeleton and controlled red

In a disposable detached worktree at the exact anchor:

```text
git apply --check skeleton.patch                         exit 0
git apply skeleton.patch                                exit 0
PYTHONPATH=<worktree>/assay/src python -m pytest ... -q exit 1
18 failed, 2 passed in 1.73s
```

The eighteen failures are the unimplemented preparation/materialization cases
and stop on explicit P22 TODOs. One pass is the exact constructor/timeout
grammar already transferred into the skeleton; the other is the hand-authored
v4 snapshot-limit document against direct raw, Schema, and merged verifier
layers. No production tree was edited and the disposable worktree was removed
afterward.

### Lint

```text
P22: L10 warning only — 6837 tokens
P23–P32: clean
```

The size warning is intentionally accepted for this 2b packet; the current
handoff and every remaining wave handoff have no lint errors.

### Locked asset hashes

| asset | SHA-256 |
|---|---|
| `README.md` | `7b118974ea7bff4ef713638ae0844b5656af25c3ab41651fd817d73ef1460cb6` |
| `skeleton.patch` | `8709ce76a6db7f522c71abd3edd4cdff638c3437a90a7d8cebff84096b8019f3` |
| `test_acceptance.py` | `2a6328a4c9a6b6bea2f7e2c7a255480381d878b6a88c5c5db579449cfc00294a` |
| `fixture-manifest.json` | `4db9abd10290234e0ed673e8b1c1bef2f9436b2deea5a168044266e5f02cf28b` |
| `probe_snapshot_plumbing.py` | `2c10bdd4028e812a96a8bd5046bfe6b2760448f846199193092f41f9c570ad62` |
| `expected/r0-snapshot-limit-v4.json` | `5b7f3cfc039b01c6d68e2169575b4580f3fd141cefa468fbc19f1088c91056a2` |

### Mechanical controller action

After the commit containing this report, the controller may dispatch P22 only
from that exact commit, verify every hash above, apply the skeleton once in the
P22 worktree, and route to Opus xhigh. It must not dispatch P23, run a
registered gate before review, or let implementation/reviewer edit the locked
directory. A named contract failure or forbidden-file need is a mechanical
P22 `BLOCKED`, routed back to Sol.
