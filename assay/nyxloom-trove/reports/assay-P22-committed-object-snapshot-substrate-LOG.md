# P22 — committed-object snapshot substrate (implementation LOG)

Package: `assay-P22-committed-object-snapshot-substrate`
Implementer: Opus xhigh, forked from package-neutral base `I-opus-1` (epoch 2)
Orientation anchor: `d82e9c026cee6043cdc86ede54cc585d68485119`
Worktree: `/workspaces/vbpub/.worktrees/assay-P22-committed-object-snapshot-substrate`
Branch: `feat/assay-P22-committed-object-snapshot-substrate`

## 1. State reconciliation (before any edit)

| check | result |
|---|---|
| worktree | `/workspaces/vbpub/.worktrees/assay-P22-committed-object-snapshot-substrate` (declared) |
| branch | `feat/assay-P22-committed-object-snapshot-substrate` |
| `git rev-parse HEAD` | `d82e9c026cee6043cdc86ede54cc585d68485119` — equals expected current HEAD |
| orientation anchor | `d82e9c026cee6043cdc86ede54cc585d68485119` — anchor **is** HEAD |
| `git diff --name-status <anchor>..HEAD` | empty — zero drift since orientation |
| `git status --porcelain=v1` | clean (0 records) |
| `main` tip | `d82e9c02` (same commit) |

Because the anchor-to-HEAD delta is empty, every doctrine and trove file the
frozen base read is current at identical blob OIDs; no stale-context reread was
required. Package-specific material (this handoff, the JIT report, the locked
assets, P20's `git.py`, `errors.py`, the conformance audit, P21's brief) was read
in this fork, not inherited.

### Locked asset verification

All six SHA-256 values match `reports/assay-P22-JIT-CARVE.md` exactly:

| asset | SHA-256 | matches report |
|---|---|---|
| `README.md` | `7b118974ea7bff4ef713638ae0844b5656af25c3ab41651fd817d73ef1460cb6` | yes |
| `skeleton.patch` | `8709ce76a6db7f522c71abd3edd4cdff638c3437a90a7d8cebff84096b8019f3` | yes |
| `test_acceptance.py` | `2a6328a4c9a6b6bea2f7e2c7a255480381d878b6a88c5c5db579449cfc00294a` | yes |
| `fixture-manifest.json` | `4db9abd10290234e0ed673e8b1c1bef2f9436b2deea5a168044266e5f02cf28b` | yes |
| `probe_snapshot_plumbing.py` | `2c10bdd4028e812a96a8bd5046bfe6b2760448f846199193092f41f9c570ad62` | yes |
| `expected/r0-snapshot-limit-v4.json` | `5b7f3cfc039b01c6d68e2169575b4580f3fd141cefa468fbc19f1088c91056a2` | yes |

## 2. Scope reconciliation (recorded, not improvised)

The controller dispatch enumerated the touch set as `git.py`, `isolation.py`,
`tests/test_isolation.py`, `tests/fixtures/isolation/**`, `README.md`,
`docs/DESIGN-GUIDE.md`, while also stating "scope touch is limited to the
handoff's declared paths" and "the handoff and JIT report are authoritative".

The handoff's own `scope.touch` additionally declares
`tests/test_verdict_conformance.py`,
`tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json`, and
this LOG path. Work item 5, the §"Complete artifact obligation inherited from
P21" section, the traceability matrix row 6, and JIT report §4 all require them
(P21 reviewer disposition `SB-P21-R2`). The dispatch's forbidden list names
"verdict, verify" — which the handoff spells as `src/assay/verdict.py` and
`src/assay/verify.py`, both of which stay untouched.

Resolution: the handoff's declared `scope.touch` governs, as the dispatch itself
directs. `src/assay/schemas/**`, `verdict.py`, and `verify.py` are NOT touched.
This is recorded rather than silently assumed.

## 3. Witnessed controlled baseline (before production edits)

Skeleton applied exactly once from the current checkout:

```text
git apply --check assay/nyxloom-trove/carve-assets/P22/skeleton.patch   exit 0
git apply       assay/nyxloom-trove/carve-assets/P22/skeleton.patch     exit 0
git status --porcelain=v1  ->  ?? assay/src/assay/isolation.py
```

Locked suite, unmodified, in the foreground:

```text
PYTHONPATH=assay/src python -m pytest --override-ini=pythonpath= \
  assay/nyxloom-trove/carve-assets/P22/test_acceptance.py -q

18 failed, 2 passed in 1.73s        (real 0m2.515s)
```

This reproduces the JIT report's recorded controlled red (`18 failed, 2 passed
in 1.73s`) exactly. Every failure terminates at an explicit
`NotImplementedError("P22 TODO: ...")` in the skeleton, i.e. at the
preparation/materialization bodies and nowhere else.

The two passing cases are the ones the carver states are already green:

- `test_limits_and_spec_refuse_values_a_caller_would_otherwise_invent` — the
  constructor/timeout grammar the skeleton already implements.
- `test_snapshot_limit_complete_artifact_is_independently_valid` — the
  hand-authored complete v4 document against the direct raw `_check_*` layer,
  the packaged Schema, and merged `verify_document`.

The 18 red cases are: nested hostile exactness, parallel repeated units,
prepared-seed source independence, linked-worktree gitfile, 2 unsafe-symlink
params, gitlink/malformed tree, 7 limit-axis params, 3 source-topology params,
and deterministic replacement.

## 4. Construction, and the plumbing probed before writing it

The private construction plan was fixed before the carver's tracer was read
(the handoff defers that read deliberately). Five load-bearing facts were then
probed against a real git 2.55.0 binary, in the foreground, before production
code existed:

| probed fact | result |
|---|---|
| `rev-list --objects --no-object-names` exists | yes — closure with no display paths |
| `rev-parse --path-format=absolute --git-common-dir` exists | yes — needed for the linked-worktree case |
| raw tree mode spelling for a directory | **`40000`**, not `040000` (the documented spelling is the human one) |
| `pack-objects --stdout` (no `--revs`) piped to private `index-pack --stdin` | 223-byte pack, closure verified in the private seed |
| `git init --template=<empty dir>` | **0 hook entries** — satisfies the locked no-hooks assertion without post-hoc cleanup |
| `read-tree` + direct byte writes + `update-ref --no-deref HEAD` | detached HEAD at the exact commit, `status --porcelain` empty |
| `commit-tree` under fixed identity, run twice | identical child OID, exact parent, message bytes `assay snapshot replacement\n` |

The last probe also produced the useful negative that justifies the closed
environment: with the ambient `GIT_AUTHOR_DATE=now` the same command dies with
`fatal: invalid date format: now`.

After the plan was fixed, the carver's tracer was read. It agreed on every
point and supplied two refinements adopted here (`--batch-check=%(objectname)
%(objecttype) %(objectsize)`, and the commit message delivered on stdin). One
deliberate divergence: the tracer builds its manifest with `ls-tree`, while the
handoff mandates **raw tree-object parsing**. The tracer is evidence, not
production code, and says so; `isolation.py` implements the raw grammar.

### What landed

- `src/assay/git.py` — a P22 section appended beneath every P20 behaviour,
  which is unchanged. Adds: an explicit-git-dir/work-tree command form (a
  private seed has no `.git` marker to discover, and discovery is exactly the
  ambient identity A-173 removed); bounded stdin; a two-process streaming pack
  relay that counts compressed bytes in transit; one monotonic deadline per
  call with process-group kill; source-topology refusal on the **common** Git
  directory; empty-template private `init`; and the fixed replacement identity.
  Children run with `start_new_session=True` so a deadline kills the whole
  group, and the pump owns a duplicated stdin descriptor so closing it to
  signal EOF cannot race a reused descriptor number.
- `src/assay/isolation.py` — the seven-phase preparation, the raw tree walker,
  direct byte materialization, independent concurrent contexts, the replacement
  child, and fail-closed cleanup.
- `tests/test_isolation.py` + `tests/fixtures/isolation/**` — ordinary
  production tests and two hand-authored literal fixtures.
- `tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json` —
  the locked v4 document, copied byte-for-byte (SHA-256
  `5b7f3cfc039b01c6d68e2169575b4580f3fd141cefa468fbc19f1088c91056a2`, verified
  by `cmp`), with the pair removed from `EXCLUDED_ENTIRELY`.
- `docs/DESIGN-GUIDE.md` — one new §6 subsection for the substrate that landed,
  plus a correction to a now-stale forward reference that still said P23 would
  introduce committed snapshots.
- No `assay/README.md` exists in this project. It appears in `scope.touch`, but
  A-127 already established that scope is permission rather than obligation and
  no oracle names it, so one was not invented.

### Two guards deleted rather than left uncoverable

Both were written defensively and then proven unreachable, which AUTHORING
§3b.D says to restructure away rather than exclude:

- a file/directory prefix-collision sweep — a leaf's parent is only ever
  reached by traversing it as a tree, so the duplicate-path check already
  refuses any name arriving as both;
- a tree-cycle guard — a cycle would require a SHA-1 hash cycle, since a
  tree's name is the hash of its content. Traversal is bounded by
  `max_entries`, which is a real ceiling. Repeated subtree OIDs at different
  paths are an ordinary DAG and still traverse normally.

## 5. Results

| suite | command | result |
|---|---|---|
| locked acceptance (unmodified) | `PYTHONPATH=assay/src pytest --override-ini=pythonpath= nyxloom-trove/carve-assets/P22/test_acceptance.py -q` | **20 passed** |
| project suite | `PYTHONPATH=src pytest --override-ini=pythonpath= tests -q` | **2198 passed, 1 skipped** |
| both together | as above, combined | **2218 passed, 1 skipped** |
| P20 Git boundary regression | `tests/test_git_*.py tests/test_errors.py tests/test_verdict_conformance.py` | 209 passed |

Baseline for comparison: P21's recorded cockpit suite was `2129 passed, 1
skipped`. The registered container gate was **not** run — the controller owns
it, its log, digest, markers and verdict.

Coverage of the two changed modules, branch-enabled:

| measurement | `isolation.py` | `git.py` |
|---|---|---|
| project suite alone | 91% | 89% |
| project suite + locked acceptance | **95%** | **93%** |

For calibration, under the project suite alone the package is not at 100%
either: P21's own new `output.py` is 91%, `verify.py` 95%, `verdict.py` 98%.
The established pattern is production tests plus the carver-owned locked
acceptance, and these modules land at or above the immediately preceding
package's standard. The residue is dominated by defensive integrity checks that
only fire if git returns something self-contradictory, and by pipe-level error
paths (`BlockingIOError`, `BrokenPipeError`, kill-race handling) that cannot be
driven deterministically without a stub so heavy the test would assert the stub.
No `no cover` pragma was added anywhere; the gate rejects them and so does this
package.

## 6. Traceability — the handoff's matrix with actual tests

| work / owner | oracle | production test(s) | locked observable |
|---|---|---|---|
| one-time source closure / `git.py` | O1/O2 | `test_a_lying_rev_list_is_refused_rather_than_trusted`, `test_a_lying_batch_check_is_refused_rather_than_trusted` | `test_prepared_seed_never_reopens_the_source_repository`, `test_linked_worktree_gitfile_resolves_the_common_object_store` |
| private concurrent materialization / `isolation.py` | O1/O2 | `test_repository_root_project_materializes_every_literal_exactly`, `test_ambient_git_environment_never_crosses_the_p22_boundary`, lifecycle tests | `test_materialization_is_safe_for_parallel_repeated_units`, `test_nested_hostile_repository_is_exact_private_clean_and_independent` |
| raw tree/path/mode/symlink validation / both | O2/O3 | `test_raw_tree_grammar_*`, `test_hostile_tree_entry_names_are_refused`, `test_a_duplicate_path_in_one_tree_is_refused`, `test_a_non_utf8_symlink_target_is_refused`, `test_contained_symlink_targets_are_accepted`, `test_absolute_empty_or_escaping_symlink_targets_are_refused`, `test_a_non_canonical_tree_mode_spelling_is_refused_end_to_end` | `test_absolute_or_escaping_symlink_is_refused_before_yield`, `test_gitlink_and_malformed_tree_are_refused_as_git_state` |
| all fixed bounds / both | O3 | `test_object_limits_refuse_the_first_overrun`, `test_limits_reject_incoherent_and_non_integer_bounds`, `test_a_bounded_stdout_ceiling_refuses_an_oversized_child` | `test_each_limit_plus_one_is_a_typed_non_yielding_refusal` (7 axes) |
| source topology / `git.py` | O1/O2 | `test_further_external_or_incomplete_topologies_are_refused` (alternates-symlink, alternates-fifo, grafts, sha256, promisor), `test_an_empty_alternates_file_is_not_an_external_store` | `test_external_or_incomplete_source_object_topology_is_refused` (alternates, partial, shallow) |
| fixed child / both | O4 | `test_replacement_identity_is_stable_across_independent_preparations`, `test_replacing_an_executable_preserves_its_mode_in_the_child`, `test_a_replacement_never_writes_to_the_prepared_seed`, `test_an_absent_replacement_path_is_a_stale_mutation_site`, `test_a_replacement_naming_a_symlink_is_a_stale_mutation_site` | `test_replacement_is_repo_relative_exact_deterministic_and_non_mutating` |
| pre-yield verification / `isolation.py` | O2 | `test_the_pre_yield_verification_catches_a_damaged_snapshot` (head, dirty, alternates, hooks, config) | — |
| lane budget / both | O3/O4 | `test_an_injected_budget_expiry_is_a_lane_timeout` | invalid-timeout grammar case |
| v4 reachability / tests only | O3 | `tests/test_verdict_conformance.py` with the exclusion removed | `test_snapshot_limit_complete_artifact_is_independently_valid` |

## 7. Controlled breaks (bounded adversarial harness)

One temporary mutation at a time, the narrowest owning test, a 300 s
process-group failsafe (hang only — a timeout is `PROBE_INCONCLUSIVE_HUNG` and
never the expected red), byte-exact restoration verified by SHA-256 after every
probe. The harness lived outside the repository and is not a deliverable. No
probe hung; every mutation restored cleanly.

| # | break | owning test | red |
|---|---|---|---|
| B1 | materialize from the source store instead of the seed | `test_prepared_seed_never_reopens_the_source_repository` | **1 failed** |
| B2 | stop refusing a non-empty local `objects/info/alternates` | `test_external_or_incomplete_source_object_topology_is_refused` | **1 failed**, 2 passed |
| B3 | drop the `max_entries` ceiling | `test_each_limit_plus_one_is_a_typed_non_yielding_refusal` | **1 failed**, 6 passed |
| B4 | accept every symlink target | `test_absolute_or_escaping_symlink_is_refused_before_yield` | **2 failed** |
| B5 | `shutil.copyfile` → `os.link` for seed packs | `test_nested_hostile_repository_is_exact_private_clean_and_independent` | **1 failed** |
| B6 | stop comparing `expected` with the committed blob | `test_replacement_is_repo_relative_exact_deterministic_and_non_mutating` | **1 failed** |
| B7b | fixed child identity: `GIT_AUTHOR_DATE` → `now` | `test_replacement_is_repo_relative_exact_deterministic_and_non_mutating` | **1 failed** |
| B7c | fixed child identity: `identity=True` → `False` | `test_replacement_is_repo_relative_exact_deterministic_and_non_mutating` | **1 failed** |
| B7a | environment closure: merge `os.environ` into the child env | `test_ambient_git_environment_never_crosses_the_p22_boundary` | **1 failed** |
| B8 | delete the new v4 snapshot-limit fixture | `tests/test_verdict_conformance.py` | **1 failed**, 160 passed |

### The probe that did NOT discriminate, and what it exposed

The first attempt at the "fixed child identity" break was B7a (merge
`os.environ` into every child environment) run against the **locked**
replacement test, which mounts a hostile ambient author/date. It came back
**GREEN**. That is a real finding, not a harness bug: the fixed identity is
applied *last* and covers all six author/committer variables, so merging the
ambient environment cannot change the child OID. Environment closure therefore
does **not** protect identity — the explicit override does; what closure
protects is object location, index location, and replace-ref base, none of
which the locked test varies.

Two things were done rather than recording a non-discriminating probe:

1. fixed child identity was re-broken at the property that actually owns it
   (B7b non-fixed date, B7c identity not applied) — both red; and
2. a production test was added for the property the locked suite could not see
   (`test_ambient_git_environment_never_crosses_the_p22_boundary`, which sets
   hostile ambient `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`,
   `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
   `GIT_REPLACE_REF_BASE` and `GIT_CONFIG_*`). B7a re-run against that test is
   **red**.

## 8. Defect found in this package's own first implementation

`test_closing_the_outer_context_with_a_live_child_is_programmer_misuse` (an
ordinary test, not a locked one) caught a real leak: closing the prepared
context while a materialization was still open raised the correct
`RuntimeError` but **abandoned the snapshot directory inside caller-owned
scratch**. That is precisely the silent state leak the handoff's terminal table
forbids. Fixed by tracking live snapshot roots and removing them before the
programmer error is raised; the refusal is still loud, and scratch is empty
afterwards. No locked test covers this shape.

## 9. Self-review against every oracle

- **O1 (one bounded transfer; repeated contexts after the source is gone).**
  `prepare_snapshot` runs the seven phases in order; the source is touched only
  in phases 1-5. Proven by the locked source-removal test (source `.git`
  renamed away, then a base *and* a replacement still materialize exactly) and
  by B1 going red. Topology and project prefix are preserved: the nested
  `apps/p` fixture reads tracked `shared/`.
- **O2 (independent, inert, private).** Disjoint object inodes across source
  and both siblings (B5 red), no alternates, no hooks, no source path or
  `filter.trap` in config, sentinel never created, ignored regular file, FIFO
  and socket all absent, concurrent sibling mutation independent, six parallel
  units. Linked-worktree gitfile resolves through the **common** dir.
- **O3 (raw grammar, bounds, refusals before yield).** Raw tree parsing with
  four hand-written malformed shapes; gitlink, `100664`, non-canonical
  `040000`, `.git`, `.`/`..`, non-UTF-8 name, duplicate path, blob-declared-as-
  tree, non-UTF-8 symlink target; UTF-8/newline/backslash path bytes survive
  byte-exact; seven independent limit axes each return the exact pair and leave
  scratch empty; eight source-topology spellings refused. The v4 snapshot-limit
  document passes the direct raw `_check_*` layer, the packaged Schema and
  merged `verify_document` (SB-P21-R1 honoured — the raw checkers are called
  directly, since the merged result cannot say which layer caught what).
- **O4 (deterministic repo-relative replacement).** Repo-top-relative path
  proven by the locked test's `input.txt` case (the project is at `apps/p`);
  exact parent, clean status, identical child OID on repeat, base unchanged
  while the child context is open, source digest unchanged, hostile ambient
  identity ineffective, executable mode preserved, stale/absent/non-regular
  descriptors all `MUTATION_DISCOVERY_FAILED`, and determinism additionally
  shown across two *independently prepared* seeds.

Not claimed: the registered container gate was not run, and no statement here
should be read as a gate verdict.

## 10. Successor candidates

```yaml
- id: SB-P22-01
  text: "prepare_snapshot is one-per-lane and its context must outlive every
    materialization. Closing the outer context while a child is open raises
    RuntimeError (the leaked child directories are removed first). P23 must
    close/join every snapshot context before leaving the prepared context —
    including on the error path — or a correct lane will die with a programmer
    error instead of its real terminal."
  evidence_ref: "isolation.prepare_snapshot finally-block; tests/test_isolation.py::test_closing_the_outer_context_with_a_live_child_is_programmer_misuse"
  audience: implementer
  applies_to: [P23]
  proposed_disposition: one-hop
  invalid_if: "the lifecycle contract in the P22 handoff's owned-interface section changes"

- id: SB-P22-02
  text: "Each materialization COPIES the seed's pack files to get independent
    inodes, so a lane's disk cost is O(units x pack bytes), not O(pack bytes).
    For the vbpub-scale closure the carver measured (~22 MiB packed) that is
    tens of MiB per mutant. P23 should account for it in scratch sizing; the
    handoff permits a verified distinct-inode reflink as the optimization, and
    hardlinking is forbidden (it breaks the isolation O2 asserts)."
  evidence_ref: "isolation._copy_objects; controlled break B5 (os.link) turns the locked inode-disjointness assertion red"
  audience: implementer
  applies_to: [P23]
  proposed_disposition: one-hop
  invalid_if: "a reflink path lands, or the seed stops being a packed store"

- id: SB-P22-03
  text: "The closed child environment does NOT pin the replacement commit's
    identity — the explicit fixed identity override does, and it is applied
    last. Merging the ambient environment still produced the correct child OID.
    What closure protects is object location, index location and replace-ref
    base. Any future reviewer or successor reasoning 'the environment is closed,
    therefore the commit is deterministic' has the causality backwards."
  evidence_ref: "controlled break B7a returned GREEN against the locked replacement test; B7b/B7c red; tests/test_isolation.py::test_ambient_git_environment_never_crosses_the_p22_boundary"
  audience: reviewer
  applies_to: [P23, P29]
  proposed_disposition: promote-contract
  invalid_if: "_P22_REPLACEMENT_IDENTITY stops being applied as an override"

- id: SB-P22-04
  text: "P22 never reads a lane file, Lane, or judge.canary.target — closing
    SB-P21-02 without a second config path. P23 owns the ONE conversion from
    P21's already-normalized project-relative canary target to the repo-top-
    relative replacement path, and must not re-normalize or re-read it. The
    same applies to MutationSite paths: replacement paths are repo-top-relative
    even when project_prefix is not '.'."
  evidence_ref: "P22 handoff 'Validation and authoritative namespaces'; locked test asserts PurePosixPath('input.txt') is refused while 'shared/input.txt' succeeds with project_prefix apps/p"
  audience: implementer
  applies_to: [P23]
  proposed_disposition: one-hop
  invalid_if: "P23's own JIT carve fixes a different conversion owner"

- id: SB-P22-05
  text: "A repository that tracks any path component literally named '.git' is
    refused with ERROR/GIT_FAILED and cannot be snapshotted at all. This is
    deliberate (it would collide with the snapshot's own Git namespace), but it
    is a consumer-visible adoption boundary that no verdict field currently
    explains, and real qualification targets should be checked for it before
    they are declared adoptable."
  evidence_ref: "isolation._build_manifest '.git' refusal; tests/test_isolation.py::test_hostile_tree_entry_names_are_refused[dot-git]"
  audience: carver
  applies_to: [P25, P28]
  proposed_disposition: decision
  invalid_if: "a qualification target is found that legitimately tracks such a path"

- id: SB-P22-06
  text: "SB-P21-R2 is CLOSED here: BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED is a
    real producer terminal with the locked complete artifact copied byte-for-byte
    into tests/fixtures/verdicts/. EXCLUDED_ENTIRELY now holds two pairs;
    NO_MEASUREMENT/MISSING_EXTERNAL_TOOL remains P27's obligation and
    ERROR/OUTPUT_WRITE_FAILED remains argued-unfixturable by A-181."
  evidence_ref: "tests/test_verdict_conformance.py EXCLUDED_ENTIRELY; controlled break B8 turns the audit red when the fixture is removed"
  audience: controller
  applies_to: [P27]
  proposed_disposition: promote-contract
  invalid_if: "P27 closes its own pair, leaving one entry"
```

## 11. Scope

Touched exactly: `src/assay/git.py`, `src/assay/isolation.py`,
`tests/test_isolation.py`, `tests/fixtures/isolation/**`,
`tests/test_verdict_conformance.py`,
`tests/fixtures/verdicts/r0_budget_exceeded_snapshot_limit_exceeded.json`,
`docs/DESIGN-GUIDE.md`, and this LOG — all inside the handoff's declared
`scope.touch`. Every forbidden path is untouched, including
`nyxloom-trove/carve-assets/P22/**` (all six SHA-256 values re-verified
identical after the adversarial harness), `src/assay/schemas/**`,
`verdict.py`, `verify.py`, `config.py`, `runner.py`, `mutation.py`,
`canary.py`, `attestation.py`, `adapters/**`, `pyproject.toml`, `assay.toml`,
`tools/**`, and `nyxloom-trove/nyxloom.toml`. No lane wiring, verdict, schema,
adapter, mutation, canary or runner behaviour changed. P23 was neither
implemented nor dispatched.
