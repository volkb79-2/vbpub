# B006(a) carve — repository snapshots with exact unsafe-symlink omissions

## 1. The problem

Assay currently refuses every R1/R2/R3 lane when any tracked symlink anywhere in the resolved commit has an absolute or repository-escaping target, even when the lane belongs to a different project and its command neither uses nor judges that link; this makes CMRU's higher-rigor evidence structurally impossible while Topos's deliberate `/etc/passwd` fixtures remain tracked, forced dstdns to delete a legitimate nginx-container symlink, and is hidden by green R0-only lanes, yet simply restricting materialisation to a project's source tree would also be wrong because real CMRU tests read and execute tracked repository-root files outside `cmru/`.

## 2. The exact property

**For each higher-rigor unit using omission mode, Assay initially hands the command a private worktree in which every declared, commit-validated P22-unsafe symlink is absent and every other P22-supported tracked path from the resolved commit is materialised.**

It does **not** give any of the following:

- It is not a project ownership boundary. Ordinary files and safe symlinks under sibling projects remain materialised.
- It is not a confidentiality, filesystem, execution, or network sandbox. `runner.default_process_runner` is still a bare `subprocess.run(..., cwd=snapshot.project_root)`.
- It does not remove the omitted link's blob or any other committed byte from the private Git object closure. `git show HEAD:<omitted-path>` still works.
- It does not stop the command clearing a skip bit and running `git checkout -- <omitted-path>`, or creating another worktree, and thereby restoring an omitted link.
- It does not prove the command is independent of an omitted link. A command may fail, skip tests, or behave differently because the link is absent; the coverage/mutation/canary claims remain responsible only for what their existing contracts say they judged.
- It does not permit arbitrary file or directory exclusions. A regular file, tree, gitlink, or P22-safe symlink cannot be named as an omission.
- It does not hide the rest of the commit from P22's structural walk. An unrelated gitlink, unsupported mode, non-UTF-8 tree name, unreadable object, or repository-wide closure limit can still refuse the lane.
- It does not promise that the omitted pathname remains absent after the command starts, or that a daemonised child cannot outlive Assay's checks.

## 3. The design

### 3.1 Authority change required before implementation

This carve deliberately replaces the project-prefix-plus-inputs design in A-266 and narrows A-267/A-268; it does not pretend those binding rows already say this. Work item 0 must record the following ruling as A-269 before code lands:

> **A-269 — B006(a) ships an explicit repository snapshot policy with exact, commit-validated omissions of P22-unsafe symlink leaves, not a project-prefix-plus-inputs boundary.** The policy is a materialisation selection, never a statement about process reachability. A-266's required `snapshot_scope = "project"`, `boundary_prefix`, `inputs`, expanded input attestation, and five boundary-containment preflights are withdrawn. A-267's no-sandbox ruling and A-268's full-index-plus-`skip-worktree` mechanism remain binding in their narrower applicable form. Verdict v6 records the selected policy and its exact declared omissions, not an execution-phase/materialisation-state enum.

If that row is not accepted and present in `decisions.md`, implementation is mechanically **BLOCKED**: A-266 otherwise requires a mutually incompatible public contract. The task brief authorises this design pass to revisit the sketched solution; it does not authorise an implementer to silently contradict the decisions ledger.

### 3.2 Configuration surface

The lane-file schema becomes version 2. These are the only two legal higher-rigor forms:

```toml
schema_version = 2

[lanes.full.isolation]
snapshot_selection = "repository"
```

```toml
schema_version = 2

[lanes.cmru.isolation]
snapshot_selection = "repository-minus-unsafe-symlinks"
unsafe_symlink_omissions = [
  "topos/tests/fixtures/inspect_files/_danger/passwd_link",
  "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
  "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current",
]
```

The exact grammar is:

- `[lanes.<name>.isolation]` is required when `rigor` contains any of `R1`, `R2`, or `R3`, and forbidden for an R0-only lane. There is no default and no inference from the `assay.toml` location.
- `snapshot_selection` is required and closed to exactly `"repository"` and `"repository-minus-unsafe-symlinks"`.
- Under `"repository"`, `unsafe_symlink_omissions` is forbidden.
- Under `"repository-minus-unsafe-symlinks"`, `unsafe_symlink_omissions` is required and contains 1 through 64 strings. Empty omission mode is refused; use `"repository"` instead.
- Each omission is the exact Git-tree pathname of one symlink leaf, relative to the repository top. It is never project-relative. Existing `SnapshotSpec.project_prefix` and `refuse_lane(project_prefix=...)` retain their existing, different meaning: the project root's repo-top-relative identity.
- Each spelling must be non-empty, non-absolute, at most 4096 UTF-8 bytes, use `/`, contain no empty, `.`, `..`, `.git`, backslash, or NUL component, and equal `PurePosixPath(raw).as_posix()` byte-for-byte. Assay refuses rather than normalises `./x`, `x//y`, `x/`, or any other alternate spelling.
- The list must be strictly ascending by the UTF-8 bytes of the canonical spelling. This makes duplicate rejection and artifact comparison mechanical; the loader does not silently sort it.
- No overlap rule is needed: commit validation requires every declaration to be a symlink **leaf**, so no declared path can be the ancestor of another path in the same Git tree.

`config.py` adds a frozen `IsolationConfig(snapshot_selection, unsafe_symlink_omissions)` whose `__post_init__` enforces the closed selection/list/path grammar for both loader and direct construction. Both constructor arguments are required; repository selection carries the derived internal empty tuple, while `as_declared()` omits the forbidden TOML key rather than serialising `[]`. It adds required, **non-defaulted** `Lane.isolation: IsolationConfig | None` immediately before today's defaulted `env_required`; every direct constructor must say `None` or supply the object. `_OPTIONAL_LANE_FIELDS`, `_load_lane`, and `Lane.as_declared()` all consume it. The loader converts structural errors to `LaneConfigError` and additionally enforces the R0/R1+ conditional. `config.py` does not import Git and does not claim to validate the named commit object.

The lane schema bump is separate from verdict schema v6 (A-065). All editable live lane literals migrate to v2 in the same commit. R0-only lanes gain only `schema_version = 2`; higher-rigor lanes explicitly choose a selection. Historical frozen carve assets are not rewritten to pretend they were authored for v2.

### 3.3 Commit validation and materialisation

The mechanism changes P22 in one narrow place while retaining its full-commit walk and complete closure:

1. `SnapshotSpec` gains a required, no-default field `snapshot_policy: IsolationConfig`. Runner adds `_snapshot_policy_for_lane(lane)`: it returns `None` only for a valid R0/no-isolation pair, returns the exact `lane.isolation` object for a valid R1+ pair, and raises `ERROR/BAD_LANE_CONFIG` for either impossible direct-constructor pairing. `run_lane` calls it before choosing the R0/higher path; `_run_higher_rigor_lane` passes its non-`None` result into `SnapshotSpec` by identity. Later WI-4's `assemble_verdict` uses this same helper. `isolation.py` imports `IsolationConfig` from `config.py` (the dependency is one-way; `config.py` still imports neither isolation nor Git). There is one policy object, not separately normalised copies for runner and materialiser. Existing direct P22 tests explicitly construct repository policy; they do not receive a shadowing default.
2. `prepare_snapshot` still resolves the literal commit, inventories and transfers its entire reachable object closure, and `_build_manifest` still traverses the entire root tree and applies every existing mode/name/object/limit check.
3. Refactor `_check_symlink_target` around one pure classifier that returns one of `safe`, `empty`, `absolute`, or `repository-escape`. Existing repository mode maps every non-`safe` result to the existing `ERROR/GIT_FAILED` diagnostic. No kernel path resolution is involved.
4. In omission mode, `_build_manifest` resolves every declared pathname against the **commit tree**, not the caller worktree. The path must exist at that commit with mode `120000`, its blob must be readable strict UTF-8, and the classifier must return `empty`, `absolute`, or `repository-escape`. Only then is the leaf removed from the worktree manifest. A safe symlink declaration is a configuration error, not permission to create an arbitrary exclusion.
   At a pathname that is declared, the “must be mode `120000`” check runs before the generic gitlink/unsupported-mode refusal, so a declared tree, regular file, executable, or gitlink deterministically means `ERROR/BAD_LANE_CONFIG`; the same kind at an undeclared path retains P22's existing `ERROR/GIT_FAILED` behavior.
5. Every undeclared symlink still goes through the current `_check_symlink_target`. Thus a newly added unsafe link fails closed; Assay never broadens the declaration automatically.
6. Each child materialisation runs `read-tree <commit>` to retain the full index, then feeds the exact NUL-delimited omission list to `git update-index --skip-worktree -z --stdin`. It writes every manifest entry except those exact symlink leaves. Using `--stdin -z` avoids both pathname parsing bugs and an argv-size dependency.
7. Replacement snapshots update only the selected regular source blob as today. `write-tree` therefore creates a child commit that changes that blob while preserving the omitted symlink entries and every ordinary sibling entry in the tree.
8. Before yielding a child, `_verify` additionally proves all of the following: `git status --porcelain=v1 -z` is empty; `git write-tree` equals `HEAD^{tree}`; parsing `git ls-files -v -z` yields uppercase `S` for exactly the declared omission set and no other path; `os.path.lexists`/descriptor-relative `lstat` says every omitted leaf is absent; and the existing HEAD, hooks, alternates, source-reference, and project-directory checks still pass. Uppercase `S` is intentional and measured.
9. The ordinary post-command dirt and HEAD checks remain unchanged. Skip-worktree is not counted as dirt. If the command restores an omitted path after clearing its skip bit and leaves the tree clean, that is allowed by the stated property and must not be redescribed as confinement.

The manifest continues to contain every non-omitted regular, executable, and symlink entry. Directory creation may leave an empty parent directory after its sole tracked leaf was omitted; Git does not track directories, and the claimed unit is the tracked path, not an empty-directory topology.

### 3.4 One runner-level collision check, not five unreachable boundary checks

B006(b) is fixed context: before the command, Assay may create the declared coverage artifact's missing parent chain in the ephemeral snapshot. An omitted symlink must not be silently replaced by such a generated directory. Therefore, after `prepare_snapshot` has successfully commit-validated the policy but before the first `prepared.materialize()`, runner converts the project-relative coverage artifact to a repo-top-relative `PurePosixPath` and refuses if it is equal to or beneath a declared omission. The comparison is `artifact_repo_path.is_relative_to(omitted_path)`, never a string prefix and never a local `Path.resolve()` against a commit namespace. This ordering preserves P22's `GIT_FAILED` answer for an unreadable target object and its `BAD_LANE_CONFIG` answer for a declaration that is not really an unsafe symlink; the collision check only runs over validated omissions.

This branch is normally precluded for file-loaded lanes because `_load_coverage` already rejects a live symlink escape, but it is reachable through the public frozen `Lane` dataclass. Its differential test constructs that public API shape directly and expects `ERROR/BAD_LANE_CONFIG`. The code comment must call it public-API defence-in-depth; it must not be advertised as a loadable-TOML preflight.

No parallel checks are added for source roots, cwd, mutation candidates, canary targets, or B005 targets:

- For a file-loaded lane, source roots resolve to contained directories and an unsafe symlink root is already rejected by `_resolve_source_root`; cwd is the resolved project directory. A directly constructed `Lane` that violates those existing path invariants is not given a second isolation-specific validator.
- Canary and B005 targets are required to resolve to regular source files through their existing loaders/evaluators; an omission is a symlink leaf.
- Mutation candidates are derived regular files beneath source roots and `SnapshotRepository.read_regular_file`/replacement checks already reject any other mode.
- A symlink leaf cannot also be an ancestor directory in one Git tree.

Adding branches for those impossible intersections would recreate the unreachable-code defect without protecting a reachable state.

### 3.5 Refusal set and exact reason codes

No new `ReasonCode` is added.

Precedence is load grammar first, then commit/object/tree validation in P22's deterministic traversal order, then the coverage-artifact collision over the successfully validated omission set. A malformed/unreadable repository may therefore fail before a later missing declaration is diagnosed, as it does today; Assay reports the first established cause and does not aggregate speculative secondary causes.

| Condition | Outcome / reason | Where it is knowable |
|---|---|---|
| Missing/unknown `[isolation]` key, wrong selection, selection/list mismatch, empty/too-long/unsorted/duplicate/non-canonical path, too many entries, isolation on R0, or no isolation on R1+ | `ERROR/BAD_LANE_CONFIG` | `config.py` loader; malformed files have no resolved lane and therefore no verdict artifact under the existing CLI contract |
| Direct construction of a structurally invalid `IsolationConfig` | `LaneConfigError` (`ERROR/BAD_LANE_CONFIG`) before a `Lane` exists | `IsolationConfig.__post_init__`; direct-constructor differential tests cover each branch |
| A public directly-constructed `Lane` violates the R0/R1+ isolation conditional | `ERROR/BAD_LANE_CONFIG` | runner's policy resolver; direct API differential test |
| Declared omission is absent at the resolved commit, names a tree/regular/executable blob/gitlink, or names a P22-safe symlink | `ERROR/BAD_LANE_CONFIG` | `_build_manifest`, after the commit tree and target bytes are readable; runner catches it into the normal whole-lane refusal artifact |
| Validated declared omission equals or is an ancestor of the coverage artifact | `ERROR/BAD_LANE_CONFIG` | runner after `prepare_snapshot` and project→repo conversion, before any materialisation; public `Lane` API is the reachable source |
| Undeclared empty/absolute/repository-escaping symlink | `ERROR/GIT_FAILED` | existing P22 symlink validation |
| Declared or undeclared symlink target blob is missing, unreadable, or non-UTF-8 | `ERROR/GIT_FAILED` | P22 object/target read; the repository fact cannot be established |
| Existing malformed tree name/mode, gitlink, object mismatch, private-index verification failure, or cleanup failure | existing `ERROR/GIT_FAILED` | existing P22 sites, unchanged |
| Existing object/entry/path/pack limit exceeded | `BUDGET_EXCEEDED/SNAPSHOT_LIMIT_EXCEEDED` | existing P22 sites, unchanged |
| Coverage-artifact parent is a P22-safe materialised symlink | `ERROR/UNREADABLE_ARTIFACT` with B006(b)'s setup diagnostic | B006(b)'s descriptor-relative `O_NOFOLLOW` parent creation/opening; not redesigned here |

### 3.6 Symlink outcome matrix

| Case | `repository` | `repository-minus-unsafe-symlinks` | Why |
|---|---|---|---|
| Tracked link with absolute target outside any project, e.g. Topos `passwd_link -> /etc/passwd` | `ERROR/GIT_FAILED` before the command | Listed: target is validated as absolute, leaf is absent and index entry is `S`. Unlisted: `ERROR/GIT_FAILED`. | This is the primary consumer block. The link is omitted, not merely ignored during validation. |
| Relative target escapes the **repository**, e.g. `other/out -> ../../etc/passwd` | `ERROR/GIT_FAILED` | Listed: absent/`S`; unlisted: `ERROR/GIT_FAILED`. | It is the other current P22-unsafe class. |
| Relative target escapes `cmru/` but remains inside the repository | Materialised if lexically repository-contained | Still materialised; listing it is `ERROR/BAD_LANE_CONFIG`. | This design intentionally has no project boundary. A sibling dependency is retained automatically. |
| Link and target both inside the repository/scope, e.g. `project/inside -> app.py` | Materialised and works | Materialised and works; listing it is `ERROR/BAD_LANE_CONFIG`. | Safe links are ordinary tracked content, not exclusions. |
| A symlink that is a declared input | There is no `inputs` key in this design. A safe link is already present; an unsafe link refuses. | There is still no input category. If the command needs the unsafe link itself, this mode is unsuitable because the only legal declaration omits it. | Eliminating an incomplete dependency inventory is a feature of this smaller contract, and its limitation is explicit. |
| Symlink is an ancestor of the coverage-artifact path | Escaping link normally fails config/P22; a safe link reaches B006(b) and is refused `ERROR/UNREADABLE_ARTIFACT` by `O_NOFOLLOW`. | An omitted-link/artifact collision is `ERROR/BAD_LANE_CONFIG` before B006(b); a safe link cannot be omitted and receives the same B006(b) refusal as repository mode. | B006(b) must never replace an omitted committed link with a generated directory. |
| Safe link points to a path present in the commit but omitted from the worktree, e.g. `project/through-omitted -> ../other/absolute-out` | Repository mode refuses on the unsafe target link elsewhere. | The safe link is materialised but initially dangling because its target leaf is absent. If the command restores the omitted leaf, it resolves again. | P22's check is lexical and does not promise target existence; no false “all links resolve” claim is added. |
| Dangling link with repository-contained spelling, e.g. `project/dangling -> missing.txt` | Materialised as a dangling link | Materialised as a dangling link; listing it is `ERROR/BAD_LANE_CONFIG`. | Dangling is not the same as escaping. Current P22 accepts it. |
| Dangling link whose target spelling is absolute or repository-escaping | `ERROR/GIT_FAILED` | Listed: absent/`S`; unlisted: `ERROR/GIT_FAILED`. | Target existence is irrelevant to lexical escape. |
| Empty symlink target blob | `ERROR/GIT_FAILED` | Listed: absent/`S`; unlisted: `ERROR/GIT_FAILED`. | Empty is already a distinct P22-unsafe class and remains fail-closed. |

## 4. Why this shape and not the alternatives

This is the minimum capability that fixes both measured incidents: omit the three known Topos unsafe leaves for a CMRU measurement, or omit dstdns's nginx-module link while retaining every ordinary repository dependency. It narrows the changed fact to the thing causing the false global refusal and makes that narrowing visible in v6.

The prior project boundary is dropped for four concrete reasons:

1. CMRU already proves that “project tree” and “command inputs” differ. A finite `inputs` list would have to inventory `cmru.project.sample.toml`, `cmru.release.sh`, and every future root/sibling dependency. The list cannot prove completeness because Assay does not trace file access.
2. The full closure and stock Git remain available, so the command can restore excluded paths or create another worktree. A wider omission set therefore buys no reachability guarantee; it only creates more absent dependencies while inviting a stronger-sounding attestation.
3. Arbitrary regular-file/tree omission creates a direct vacuity risk for coverage and mutation. Restricting the exception to leaves that P22 would otherwise reject prevents the mechanism from being used to hide source, tests, or B005 targets.
4. The recurring review defects—unreachable containment branches, underivable `materialisation`, overlapping path expansion, and dependency enumeration—belonged to the larger contract rather than to the measured symlink problem.

The withdrawn alternatives are resolved as follows:

- **Scope validation to `source_roots`: rejected.** It withholds legitimate test inputs and confuses “judged” with “read”. This design keeps all non-omitted repository content.
- **`allow_escaping_symlinks`: rejected.** That would materialise an unsafe link after merely suppressing its validation. This design validates that the link is one P22 would refuse and then does not write the leaf.
- **Arbitrary `exclude = [...]`: rejected.** It could hide regular source/tests and reopen B005's vacuity hole. Only P22-unsafe symlink leaves qualify.
- **Project prefix plus explicit inputs: dropped, not deferred as part of B006(a).** It solves a larger dependency-selection problem without confinement and requires consumers to declare facts Assay cannot verify are complete. A future capability may pursue it under a different name and threat model, but this feature's verdict must not imply it.
- **Pruned Git closure: rejected.** B006.3 requires the complete commit closure and P22's diff/replacement/provenance paths use it.
- **Namespace/Landlock/container sandbox: rejected by A-030 and DESIGN-GUIDE §7.** It also cannot contain CMRU's `/opt/tester-venv/bin/python` without execution-environment knowledge that Assay must not own.
- **Delete/untrack the consumer artifact: rejected as a product solution.** It is dstdns's current workaround and destroys a real vendored filesystem fact.
- **Automatically add newly encountered unsafe links to the omission set: rejected.** That silently broadens evidence. An unlisted link is `ERROR/GIT_FAILED` until the lane owner reviews and declares it.

## 5. Verdict v6 record and producers

### 5.1 Wire shape

Every lane-resolved v6 verdict whose `declared_rigor` contains R1, R2, or R3 has this required top-level object:

```json
"snapshot_policy": {
  "selection": "repository-minus-unsafe-symlinks",
  "unsafe_symlink_omissions": [
    "topos/tests/fixtures/inspect_files/_danger/passwd_link",
    "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
    "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current"
  ]
}
```

Repository mode is:

```json
"snapshot_policy": {
  "selection": "repository"
}
```

The object is absent for an R0-only verdict and for an error produced before a lane resolves. It is a **policy record**, not a claim that materialisation reached a phase. Whenever a higher-rigor command unit actually ran—whether its eventual judged claim passed or failed—the materialiser consumed the same immutable policy object recorded here, and the end-to-end differential oracle proves that wiring. On an early refusal, it truthfully records only the policy under which Assay attempted or refused the lane; the refusal does not masquerade as a completed snapshot.

No commit is repeated inside the object; top-level `commit` remains the single identity.

### 5.2 Field-by-field producer proof

| Field/value | Exact producer | Why the values are distinguishable there |
|---|---|---|
| `snapshot_policy` absent | `runner.assemble_verdict` calls `_snapshot_policy_for_lane(lane)`; it returns `None` exactly when `lane.rigor == ("R0",)` | The lane is present and its canonical rigor tuple is already used to build claims. The loader forbids `[isolation]` on this path. No Git or boundary fact is needed. |
| `snapshot_policy.selection = "repository"` | The same helper copies `lane.isolation.snapshot_selection` | Every R1+ loaded lane must carry `IsolationConfig`; the enum was closed by the loader. This branch is byte-distinct from omission mode before any snapshot begins. |
| `snapshot_policy.selection = "repository-minus-unsafe-symlinks"` | The same helper copies the other closed enum value | The config object is the same immutable object passed to `SnapshotSpec`; no exception phase has to be inferred. |
| `snapshot_policy.unsafe_symlink_omissions` present | The same helper copies `lane.isolation.unsafe_symlink_omissions` only for omission selection | The loader requires a non-empty canonical tuple in this mode and forbids the key in repository mode. Values do not depend on whether `_verify` or `prepare_snapshot` later failed. |
| `snapshot_policy.unsafe_symlink_omissions` absent | The same helper omits the key for repository selection | This is selection-driven and therefore distinguishable at every producer call site. It is not encoded as an ambiguous empty list. |

`assemble_verdict` is the single construction site today. Normal completion calls it directly. `refuse_lane` and `_refuse_lane_with_plan` also end there, covering dirty/HEAD preconditions, snapshot errors, cleanup errors, attestation timeout, and adapter refusal. In particular, both `cli.py:_run_reserved` refusal branches around the currently cited lines 355 and 385 still have the resolved `Lane`, so they can emit this policy without a repository or materialisation state. There is no module-level fallback constant.

Direct callers can construct an invalid public `Lane` that the TOML loader cannot. `_snapshot_policy_for_lane` must refuse a higher-rigor lane with no isolation, or an R0 lane with isolation, as `ERROR/BAD_LANE_CONFIG`; tests reach those branches through direct construction. It must never invent repository mode.

### 5.3 Model, schema, and independent verification

- `verdict.py` adds a frozen wire model `SnapshotPolicy`, adds `snapshot_policy` to `Verdict`, serialises it, and extends the lane-resolved invariant with the rigor conditional above.
- The schema `description` on `snapshot_policy` says verbatim: “The lane-selected initial worktree materialisation policy. Exact omissions are absent when Assay hands each unit to its command; this is not a sandbox, does not prune Git objects, and does not prevent the command restoring them.” The wire contract therefore carries its own interpretation instead of relying on this carve.
- Draft 2020-12 **can** express the local object union: `selection = repository` forbids the list; omission selection requires a 1..64 unique list. Add `$defs/repo_tree_path` rather than reusing project-relative prose, with `"type": "string"`, `"minLength": 1`, `"maxLength": 4096`, and the exact JSON-encoded pattern `"^(?!(?:\\.{1,2}|\\.git)(?:/|$))[^/\\\\\\u0000]+(?:/(?!(?:\\.{1,2}|\\.git)(?:/|$))[^/\\\\\\u0000]+)*$"`. This rejects absolute/empty/dot/dotdot/empty/backslash/NUL/`.git` components. A top-level `if` with `declared_rigor.contains` requires the object for R1+ and forbids it otherwise.
- Draft 2020-12 **cannot** express strict array sorting or a 4096-**UTF-8-byte** ceiling (`maxLength` counts Unicode code points). `Verdict` enforces strict UTF-8 encodability, the byte ceiling, and order; `verify.py` independently checks those facts on the raw list before reconstruction, including rejecting a lone surrogate that cannot be UTF-8 encoded.
- The schema cannot prove that a path is a symlink in `commit`, that its target is unsafe, or that it was absent from a worktree. Those are runtime producer facts owned by P22 and the differential integration tests; neither schema nor offline verifier claims them.
- `verify.py` hand-transcribes the selection vocabulary and conditional rather than importing the model's table, following the existing independent layer style.
- No `materialisation = none|partial|complete` field exists. The runner's shared exception state cannot distinguish all such phases, and this design does not need it: `snapshot_policy` says what was selected, while claims/outcome say whether a measurement completed.

This is the minimum §6 change needed from the already-specified verdict-v6 work: add `snapshot_policy` and its conditional. Branch payloads, B005 judgments, the `changed_executable`→`executable` rename, and every other v6 rule remain untouched.

## 6. Work items

Each item is independently committable and lands in this order.

### WI-0 — accept the product ruling

Files: `assay/nyxloom-trove/decisions.md`, `assay/nyxloom-trove/W1-CARVE-branch-coverage-and-whole-target.md`, and `assay/nyxloom-trove/W1-RESUME.md`.

Record A-269 exactly as §3.1, mark the old §1 project-boundary contract superseded rather than rewriting its history, and point the resume decision to this carve. Test with `rg` that A-266 through A-269 each occur once. If A-269 is not authorised, stop with the mechanical BLOCKED condition; do not implement around the ledger.

### WI-1 — lane schema v2 and immutable policy

Files: `assay/src/assay/config.py`; new `assay/tests/test_config_snapshot_selection.py` and `assay/tests/test_lane_schema_v2_locked_successors.py`; `assay/tools/tester-unified-gate.sh`; the editable live lane literals in `assay/assay.toml`, `cmru/assay.toml`, `assay/gate/python/qualify_topos.py`, `assay/tests/conftest.py`, `assay/tests/test_canary_python_pipeline.py`, `assay/tests/test_cli_run.py`, `assay/tests/test_config_accept.py`, `assay/tests/test_config_env_required.py`, `assay/tests/test_config_reject.py`, `assay/tests/test_config_source_roots.py`, `assay/tests/test_dependency_purity.py`, `assay/tests/test_distribution_build_release.py`, `assay/tests/test_self_hosting.py`, `assay/tests/test_standalone.py`, and `assay/tests/test_verify_layer_independence.py`; plus new audit log `assay/nyxloom-trove/reports/W1-WI1-lane-v2-migration.md`.

Implement §3.2, bump `LANE_SCHEMA_VERSION` to 2, round-trip against independent `tomllib`, and migrate each editable literal by rule: R0 gets no isolation; R1+ gets explicit repository unless the test is specifically an omission test. Tests cover every enum/requiredness/path/list bound and distinguish raw spelling from normalisation. The old “unknown version 2” test becomes “unknown version 3”.

Do not edit `nyxloom-trove/carve-assets/**`. To keep this commit green, the registered gate adds rootdir-relative deselections for the four P26 nodes that load `_lane_document` but are not already deselected, and the five P33 nodes that load `_load_lane`:

```text
nyxloom-trove/carve-assets/P26/test_acceptance.py::test_runner_binds_evidence_batch_to_lane_source_before_any_work
nyxloom-trove/carve-assets/P26/test_acceptance.py::test_r0_attestation_config_round_trips_without_inventing_a_judge
nyxloom-trove/carve-assets/P26/test_acceptance.py::test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape
nyxloom-trove/carve-assets/P26/test_acceptance.py::test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget
nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_fixture_itself_loads_today
nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_refuses_a_cross_language_operator
nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_accepts_a_matching_language_operator
nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_names_kill_signal_artifact_as_reserved_for_p34
nyxloom-trove/carve-assets/P33/test_acceptance_v5.py::test_config_names_equivalence_artifact_as_reserved_for_p34
```

`test_lane_schema_v2_locked_successors.py` carries those nine behaviors forward under v2 as nine one-for-one, similarly named tests, and the same installed-wheel gate invocation runs it; a combined omnibus successor is forbidden because it would make a lost behavior hard to see. These are version-coupled fixture failures, not permission to deselect any other red. Run both frozen modules first, record their full red list and prove it equals this nine-node addition plus already-documented verdict-version deselections; any extra red is a regression to fix. Record that output and the one-for-one mapping in the named audit log.

### WI-2 — P22 validation, omission, index invariants, and mandatory call-site wiring

Files: `assay/src/assay/isolation.py`, `assay/src/assay/runner.py`, existing `assay/tests/test_isolation.py`, new `assay/tests/test_isolation_unsafe_symlink_omissions.py`, and the `SnapshotSpec`/runner fixture builders in `assay/tests/conftest.py`. Do not edit frozen coverage fixtures.

Implement §3.3, migrate every **editable** direct `SnapshotSpec` construction (`runner.py`, `tests/conftest.py`, and `tests/test_isolation.py`) to an explicit repository policy, and in the same commit pass `lane.isolation` into the one `SnapshotSpec` that creates the shared `SnapshotRepository`. The locked P22/P23 carve assets remain byte-identical historical clients and are not imported by the registered gate; their v1 API calls are not silently described as current. This mechanical runner edit is required here: landing a no-default `SnapshotSpec.snapshot_policy` without updating its production caller is not independently committable. Baseline, mutants, and both canary halves already consume that shared repository; do not add separate policy parameters to mutation/canary entry points. The test matrix includes all §3.6 cases, repository/omission controls, exact uppercase-`S` parsing, status/write-tree equality, full non-omitted tracked-path materialisation, complete closure, replacement-tree preservation, source checkout non-contamination, and cleanup on each failure. Every negative has an unmodified control in the same test. Remove the omission code locally and confirm the primary matrix fails on the absolute link; remove the safe-link kind check and confirm the arbitrary-exclusion negative fails.

### WI-3 — runner wiring and B006(b) collision

Files: `assay/src/assay/runner.py`, new `assay/tests/test_runner_snapshot_selection.py`, and the already-migrated direct `Lane` builders in `assay/tests/conftest.py` and `assay/tests/test_canary_python_pipeline.py` (the complete direct-constructor set measured at this revision).

Add only §3.4's coverage-artifact collision and the cross-unit runner proof over the wiring landed in WI-2. A `process_runner` double inspects its live `cwd` and proves the three omitted leaves are absent, the two CMRU root files and an ordinary Topos file are present, `status` is clean, and the exact skip set is visible during the baseline, mutant, and both canary halves. Direct-invalid-Lane tests reach the defence branches. No changes to `evaluate.py` or `safeio.py` belong to this item.

### WI-4 — v6 policy attestation

Files: `assay/src/assay/verdict.py`, `assay/src/assay/verify.py`, `assay/src/assay/schemas/verdict.schema.json`, `assay/src/assay/runner.py`, new `assay/tests/test_verdict_snapshot_policy.py`, `assay/tests/test_verdict_transparency.py`, `assay/tests/test_verdict_conformance.py`, `assay/tests/test_verify_layer_independence.py`, `assay/tests/test_cli_run.py`, `assay/tests/fixtures/verdicts/*.json`, `assay/nyxloom-trove/carve-assets/W1/migrate_v5_to_v6.py`, `assay/nyxloom-trove/carve-assets/W1/test_acceptance_v6.py`, `assay/nyxloom-trove/carve-assets/W1/expected/*.json`, and `assay/tools/tester-unified-gate.sh`. Those globbed sets are the exact typed transform/expected buckets already owned by wave §6; no earlier frozen carve asset is edited.

Implement §5 as part of the single v6 hard cut, not as v5 or a later v7. Tests cover repository and omission successes; R0 absence; every early producer (`env_required` refusal, `refuse_lane`, attestation timeout, adapter refusal, dirty tree, HEAD drift, snapshot `GIT_FAILED`, and generic OSError mapping); schema/model/raw rejection of conditional/list/order tampering; and exact CLI round-trip. The test for an early bad commit declaration expects the **declared policy** plus `ERROR/BAD_LANE_CONFIG`, never a fabricated phase. Do not change B005 or branch semantics.

### WI-5 — real consumer qualification without the R2 gate trap

Files: new `assay/gate/python/qualify_cmru_b006a.py`, new `assay/tests/test_gate_qualify_cmru_b006a.py`, and `assay/tools/tester-unified-gate.sh`.

The harness follows `qualify_topos.py`'s installed-wheel/disposable-repository pattern and freezes these measured source inputs:

```text
INPUT_REVISION=c3b00729eb61bec3fbb4fead50218a3a4db886e2
CMRU_TREE=6fbb3c2c00be81dd893dc11ad0109d14bc846556
TOPOS_TREE=31b88ee2ff71566afa4aa23b83ddeff5799ec855
```

It refuses drift and seeds a disposable full-repository checkout without deleting any Topos link. **Premise correction:** the checked-in CMRU suite at the frozen input is not green even outside Assay; `test_release_publish_rejects_response_without_upload_coordinate` stubs `get_release_by_tag` but not `update_release`, so `publish()` makes a real GitHub request before it can test the missing-coordinate refusal. The registered tester has `--network=none`, so changing execution environments cannot make that test valid. The harness therefore first makes a qualification-baseline commit that changes no product source and adds exactly this missing test double immediately after the existing `get_release_by_tag` assignment:

```python
client.update_release = lambda *args, **kwargs: {"id": 7}
```

The harness compares the complete patched test-file bytes to “frozen blob plus that one insertion,” runs the repaired node once, and requires PASS. It then creates exactly one controlled head commit containing:

- `cmru/src/cmru/_b006a_probe.py` with exactly `def matches(value: int) -> bool:\n    return value == 7\n` (plus an optional non-executable module docstring, but no other executable expression);
- `cmru/tests/test_b006a_probe.py` importing `matches` and one test containing exactly `assert matches(7)` and `assert not matches(8)`, so the single `python:compare-swap` mutant is killed;
- a schema-v2 `cmru/assay.toml` declaring R0/R1/R2/R3, changed-lines R1, the baseline OID as `base`, source root `src/cmru`, branch-aware coverage, `jobs = 1`, `max_mutants = 2`, only `python:compare-swap`, uncovered-line canary target `_b006a_probe.py`, and the three exact Topos omissions.

The generated lane is exactly this, with `@BASE_OID@` replaced by the qualification-baseline commit's full 40-hex OID:

```toml
schema_version = 2

[lanes.cmru_b006a_qualification]
scope = "S1"
rigor = ["R0", "R1", "R2", "R3"]
enforcement = "gate"
argv = ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q", "--cov=src/cmru", "--cov-branch", "--cov-report=json:.assay/coverage.json"]
env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }
env_passthrough = []
budget = "20m"
allow_argv_append = false

[lanes.cmru_b006a_qualification.isolation]
snapshot_selection = "repository-minus-unsafe-symlinks"
unsafe_symlink_omissions = [
  "topos/tests/fixtures/inspect_files/_danger/passwd_link",
  "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
  "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current",
]

[lanes.cmru_b006a_qualification.judge]
language = "python"
source_roots = ["src/cmru"]
mode = "changed_lines"
base = "@BASE_OID@"
fail_under = 100.0
allow_excluded = false
require_branch = true

[lanes.cmru_b006a_qualification.judge.coverage]
format = "coverage-py-json"
artifact = ".assay/coverage.json"

[lanes.cmru_b006a_qualification.judge.mutation]
jobs = 1
max_mutants = 2
operators = ["python:compare-swap"]

[lanes.cmru_b006a_qualification.judge.canary]
mechanism = "uncovered-line"
target = "src/cmru/_b006a_probe.py"
```

The argv is `/opt/tester-venv/bin/python -m pytest tests -q --cov=src/cmru --cov-branch --cov-report=json:.assay/coverage.json`. It deliberately has **no** `--cov-fail-under`; `judge.fail_under = 100.0` owns the decision so the R3 transformed half fails for `UNCOVERED_LINES`, not `COMMAND_FAILED`. B006(b) creates `.assay`.

The full CMRU suite runs from that explicitly repaired disposable history, thereby exercising both measured root dependencies without concealing the stale premise. The harness independently checks: R0/R1/R2/R3 all PASS; R1 `executable > 0`; R2 `candidate_count == total == 1`, exactly one killed identity and no survivor/equivalent/budget bucket; R3 control PASS, transformed FAIL, expected and observed reason `UNCOVERED_LINES`; the v6 snapshot policy and three paths exactly match; the source repository is unchanged; and the disposable snapshot command observed all three links absent. The harness also checks that `git diff <frozen-input>..<qualification-baseline>` is exactly the one test file and that `git diff <qualification-baseline>..<controlled-head> -- cmru/src` is exactly `_b006a_probe.py`; this prevents the repair from manufacturing mutation candidates or changing consumer behavior.

This avoids the release-gate trap because the controlled head **does** change `cmru/src`, guaranteeing a real R2 candidate, and because the qualification is a dedicated Assay gate phase rather than a permanent `assay run` step in `cmru/cmru.toml`. `cmru/assay.toml` remains R0 in the real checkout until CMRU's owner chooses a per-commit R2 policy; an ordinary non-source commit therefore cannot roll the CMRU release gate to exit 5/`NO_MUTANTS`.

The unit test for the harness stubs subprocess boundaries and proves no marker on wrong input OID, surviving mutant, wrong canary reason, missing omission, or source-checkout dirt. The real harness runs only in `tester-unified`, never in the devcontainer cockpit.

Also add an in-repo integration fixture named for dstdns's exact path `infra-global/reverse-proxy/etc-nginx/modules -> /usr/lib/nginx/modules`: repository mode refuses; omission mode runs an unrelated project command while the link is absent and ordinary `infra-global` files remain. The real external dstdns checkout is measured input, not a gate dependency.

### WI-6 — product documentation and release handoff

Files: `assay/docs/DESIGN-GUIDE.md`, `assay/nyxloom-trove/1-north-star.md`, `assay/nyxloom-trove/2-product-definition.md`, `assay/nyxloom-trove/4-backlog.md`, `assay/nyxloom-trove/STATE.md`, `assay/CHANGES.md`, and new `assay/nyxloom-trove/reports/W1-WI6-B006a-implementation.md`.

Document the exact one-sentence property and every non-property from §2; mark only B006(a)'s smaller unsafe-symlink problem complete; leave project-prefix dependency selection explicitly unshipped; cite the real test node IDs and qualification marker. Release/pinning and any dstdns re-tracking are external adoption steps, not hidden implementation mutations.

## 7. Acceptance oracles

All commands below run in `tester-unified` against the installed candidate wheel. The `&& printf` marker is part of each oracle: it gives an exact success observable while preserving pytest's nonzero status. Removing the asserted behavior must suppress the marker.

### O1 — config contract

Command, from `assay/`:

```bash
/opt/tester-venv/bin/python -m pytest -q -p no:randomly \
  tests/test_config_snapshot_selection.py::test_snapshot_selection_closed_matrix \
  && printf 'ASSAY_B006A_CONFIG=1\n'
```

Exact success marker:

```text
ASSAY_B006A_CONFIG=1
```

Broken/absent observable: accepting a missing higher-rigor table, an isolation table on R0, an empty or oversized omission list, an unsorted/duplicate list, or an alternate/non-canonical path spelling makes the parametrised test fail and the marker is absent. The same test round-trips each control through independent `tomllib`; commit-dependent symlink kind is intentionally tested only by O2.

### O2 — symlink matrix and full-index mechanism

Command:

```bash
/opt/tester-venv/bin/python -m pytest -q -p no:randomly \
  tests/test_isolation_unsafe_symlink_omissions.py::test_complete_symlink_matrix_and_index_invariants \
  && printf 'ASSAY_B006A_ISOLATION=1\n'
```

Exact success marker:

```text
ASSAY_B006A_ISOLATION=1
```

Broken/absent observable: without omission support the control raises `ERROR/GIT_FAILED` at the absolute link; if validation is merely skipped, `lstat` sees the link and the test fails; with a narrow index, status/write-tree differs; with a lowercase-`s` oracle, the exact parsed set is empty rather than equal and the test fails; if safe links are excludable, the safe-link negative does not raise `ERROR/BAD_LANE_CONFIG` and the test fails.

### O3 — runner applies the same policy to baseline, R2, and R3

Command:

```bash
/opt/tester-venv/bin/python -m pytest -q -p no:randomly \
  tests/test_runner_snapshot_selection.py::test_live_command_observes_exact_policy_in_every_unit \
  && printf 'ASSAY_B006A_RUNNER=1\n'
```

Exact success marker:

```text
ASSAY_B006A_RUNNER=1
```

Broken/absent observable: the process double records one baseline, each mutant, and both canary halves; any unit that materialises an omitted leaf, loses a root dependency, has a nonempty status, or carries a different `IsolationConfig` identity fails before the marker. This inspects `cwd` while the context is live, not after cleanup.

### O4 — v6 producer and verifier are total

Command:

```bash
/opt/tester-venv/bin/python -m pytest -q -p no:randomly \
  tests/test_verdict_snapshot_policy.py::test_every_lane_resolved_producer_records_derivable_policy \
  && printf 'ASSAY_B006A_VERDICT_V6=1\n'
```

Exact success marker:

```text
ASSAY_B006A_VERDICT_V6=1
```

Broken/absent observable: the test drives normal success plus `env_required`, `refuse_lane`, CLI attestation-timeout, adapter-refusal, dirty-tree, HEAD-drift, snapshot-`GIT_FAILED`, and generic-OSError paths in repository/omission modes, plus R0; a missing object, invented empty list, phase-dependent value, or document that model/schema/raw verification disagree on fails and suppresses the marker.

### O5 — dstdns incident shape

Command:

```bash
/opt/tester-venv/bin/python -m pytest -q -p no:randomly \
  tests/test_runner_snapshot_selection.py::test_dstdns_nginx_link_is_an_exact_omittable_leaf \
  && printf 'ASSAY_B006A_DSTDNS_SHAPE=1\n'
```

Exact success marker:

```text
ASSAY_B006A_DSTDNS_SHAPE=1
```

Broken/absent observable: repository mode must produce `ERROR/GIT_FAILED`; omission mode must execute with the exact nginx link absent and an ordinary sibling present. Treating this as a validation allowlist leaves the link visible and fails. Allowing `infra-global/**` as an arbitrary tree exclusion also fails because the ordinary sibling disappears.

### O6 — real CMRU end to end

Command, inside the registered gate after the candidate wheel is installed:

```bash
PYTHONPATH= /opt/tester-venv/bin/python gate/python/qualify_cmru_b006a.py \
  --source-repo .. \
  --scratch "$scratch/b006a-cmru" \
  --current-assay "$scratch/run-venv/bin/assay" \
  --current-version "$version"
```

Exact stdout on success:

```text
ASSAY_B006A_CMRU_QUALIFIED=1
```

Broken/absent observable: without B006(a), no marker is printed and the first higher-rigor snapshot is `ERROR/GIT_FAILED` naming Topos's first `/etc/passwd` link. If the one-line qualification repair drifts or no longer makes its named node pass, the harness refuses before Assay runs. If R2 becomes vacuous, the harness sees `NO_MUTANTS` or a candidate count other than one and suppresses the marker. If `--cov-fail-under` is accidentally restored, R3 observes `COMMAND_FAILED` rather than `UNCOVERED_LINES` and suppresses the marker. If either root dependency is absent, its already-measured named test fails and there is no marker.

### O7 — registered gate receipt

Command from the repository top, with the gate's existing cgroup-parent derivation:

```bash
bash assay/tools/tester-unified-gate.sh .
```

The exact new/final success lines, in this order, are:

```text
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
ASSAY_REGISTERED_GATE_COMPLETE=1
```

Broken/absent observable: `tester-unified-gate.sh` captures the harness stdout and requires the qualification marker exactly once before printing its phase; any unit, consumer, schema, or installed-wheel failure exits under `set -e` before both the phase and final receipt. The outer script continues to pass `--cgroup-parent` from `assay/tools/cgroup-parent.sh`; no container runs unconfined.

After checking the captured value, the gate explicitly prints that value once, then prints the phase. Command substitution alone would hide the harness marker and make the three-line receipt above false.

## 8. Limitations and deferrals

- **Project-scoped evidence is not shipped.** On a verdict whose higher-rigor command unit ran, the v6 policy distinguishes complete repository materialisation from the exact narrower repository-minus-links measurement, but it does not call the latter “project”. On an earlier refusal it distinguishes the selected policies without claiming either ran. In both cases a reviewer sees every declared omission; only a command-bearing run proves those declarations were validated and applied.
- **Dependency completeness is not proved.** Keeping all non-omitted paths avoids requiring an incomplete inventory; it does not prove the command never needed an omitted unsafe link.
- **Reachability is not controlled.** Object reads, checkout restoration, alternate worktrees, absolute paths, `..`, `/proc`, external executables, network, and daemonised children remain execution-environment concerns.
- **Only existing P22-unsafe symlink target classes qualify.** Regular files, directories, safe links, non-UTF-8 targets, gitlinks, and unsupported modes cannot be omitted. Extending eligibility needs a new product ruling because it changes the evidence/vacuity risk.
- **The full tree is still structurally inspected and the full closure bounded.** This smaller capability fixes the two live symlink incidents, not every possible monorepo-wide P22 refusal.
- **The list is intentionally manual and fail-closed.** Adding another unsafe link reds omission lanes until their owners review and add its exact path. There is no discovery fallback.
- **B006(b) remains separate.** This carve adds only the collision refusal needed to stop parent creation from replacing an omitted link. Parent creation, diagnostics, permissions, and `O_NOFOLLOW` stay as already specified.
- **No materialisation phase is recorded.** It was underivable at the common exception site. The policy is always derivable; completion is conveyed by the existing claims and outcome.
- **CMRU qualification is not permanent R2 adoption.** A permanent gate over arbitrary commits still needs a consumer policy for `NO_MUTANTS`; the controlled source commit proves B006(a) without making unrelated CMRU commits exit 5.
- **The frozen CMRU input is not full-suite green as claimed in the brief.** WI-5 uses one explicit, differential test-only repair in its disposable baseline; it neither edits CMRU nor presents that repaired history as consumer evidence. Repairing the checked-in CMRU test belongs to CMRU's owner.
- **dstdns adoption is external.** This implementation permits dstdns to re-track its nginx link and declare it, but does not mutate `/workspaces/dstdns` or decide its release timing.
- **Historical lane-v1 assets remain historical.** The active lane grammar hard-cuts to v2; frozen evidence is not rewritten. Any gate deselection must name a v2 successor test.
- **No release/pin is performed by these work items.** Release remains a separately authorised state change after the registered gate and adversarial review are green.

## 9. Measurements made for this carve

All Git writes below occurred only in `/tmp/b006a-scratch/`. Commands against `/workspaces/vbpub` and `/workspaces/dstdns` were read-only.

### M1 — exact source revision and consumer trees

Command:

```bash
printf 'INPUT_REVISION=%s\n' "$(git rev-parse HEAD)"
printf 'CMRU_TREE=%s\n' "$(git rev-parse HEAD:cmru)"
printf 'TOPOS_TREE=%s\n' "$(git rev-parse HEAD:topos)"
printf 'TRACKED_COUNT=%s\n' "$(git ls-files | wc -l)"
```

Output:

```text
INPUT_REVISION=c3b00729eb61bec3fbb4fead50218a3a4db886e2
CMRU_TREE=6fbb3c2c00be81dd893dc11ad0109d14bc846556
TOPOS_TREE=31b88ee2ff71566afa4aa23b83ddeff5799ec855
TRACKED_COUNT=3185
```

### M2 — current P22 really refuses CMRU before execution

Command:

```bash
PYTHONPATH=assay/src python - <<'PY'
from pathlib import Path, PurePosixPath
from assay import git
from assay.errors import AssayError
from assay.isolation import SnapshotSpec, prepare_snapshot
repo = Path.cwd().resolve()
scratch = Path('/tmp/b006a-scratch/current-p22').resolve()
commit = git.head_rev(repo)
try:
    with prepare_snapshot(SnapshotSpec(repo_top=repo, commit=commit,
        project_prefix=PurePosixPath('cmru'), scratch_root=scratch), timeout=30):
        print('unexpected-success')
except AssayError as exc:
    print(f'{exc.outcome.value}/{exc.reason_code.value}: {exc}')
PY
```

Output:

```text
ERROR/GIT_FAILED: symlink topos/tests/fixtures/inspect_files/_danger/passwd_link targets the absolute path '/etc/passwd'
```

### M3 — the complete tracked-symlink inventory (three blockers, not one)

Command:

```bash
git ls-files -s | awk '$1 == "120000" {print $4}' |
while IFS= read -r path; do
  printf '%s -> ' "$path"
  git show "HEAD:$path"
  printf '\n'
done
```

Output:

```text
topos/tests/fixtures/inspect_files/_danger/passwd_link -> /etc/passwd
topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape -> /etc/passwd
topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current -> /etc/passwd
topos/tests/fixtures/procfs/network/1001/ns/net -> ../../ns/private-game
topos/tests/fixtures/procfs/network/1002/ns/net -> ../../ns/private-game
topos/tests/fixtures/procfs/network/2001/ns/net -> ../../ns/host
topos/tests/fixtures/procfs/network/3001/ns/net -> ../../ns/private-a
topos/tests/fixtures/procfs/network/3002/ns/net -> ../../ns/private-b
```

### M4 — current lexical symlink classification

Command:

```bash
PYTHONPATH=assay/src python - <<'PY'
from pathlib import PurePosixPath
from assay.errors import AssayError
from assay.isolation import _check_symlink_target
cases = {
    'absolute': ('other/absolute-out', '/etc/passwd'),
    'relative_escape': ('other/relative-out', '../../etc/passwd'),
    'inside': ('project/inside', 'app.py'),
    'dangling_inside': ('project/dangling', 'missing.txt'),
    'points_to_omitted': ('project/through-omitted', '../other/absolute-out'),
}
for name, (path, target) in cases.items():
    try:
        _check_symlink_target(PurePosixPath(path), target)
    except AssayError as exc:
        print(f'{name}={exc.outcome.value}/{exc.reason_code.value}: {exc}')
    else:
        print(f'{name}=accepted')
PY
```

Output:

```text
absolute=ERROR/GIT_FAILED: symlink other/absolute-out targets the absolute path '/etc/passwd'
relative_escape=ERROR/GIT_FAILED: symlink other/relative-out targets '../../etc/passwd', which escapes the snapshot root
inside=accepted
dangling_inside=accepted
points_to_omitted=accepted
```

This confirms that dangling-inside and a safe link to a later-omitted leaf need no new P22 rule.

### M5 — full index plus skip-worktree gives the required clean tree

Preparation command (in a fresh clone under `/tmp`):

```bash
git update-index --skip-worktree -- other/absolute-out other/relative-out
unlink other/absolute-out
unlink other/relative-out
```

The production-shaped NUL-input form was separately run:

```bash
printf 'other/absolute-out\0other/relative-out\0' |
  git update-index --skip-worktree -z --stdin
git ls-files -v | awk '$1=="S" {print}'
```

Output:

```text
S other/absolute-out
S other/relative-out
```

Exact inspection command against that prepared scratch repository:

```bash
probe=/tmp/b006a-scratch/final-index-yf30tx/repo
printf "status='%s'\n" "$(git -C "$probe" status --porcelain=v1)"
printf 'HEAD_TREE=%s\n' "$(git -C "$probe" rev-parse 'HEAD^{tree}')"
printf 'INDEX_TREE=%s\n' "$(git -C "$probe" write-tree)"
printf 'root_input=%s\n' "$(git -C "$probe" show :root-input.txt)"
printf 'absolute_out=%s\n' "$([ -L "$probe/other/absolute-out" ] && printf present || printf absent)"
printf 'relative_out=%s\n' "$([ -L "$probe/other/relative-out" ] && printf present || printf absent)"
printf 'inside_target=%s\n' "$(readlink "$probe/project/inside")"
printf 'dangling_target=%s\n' "$(readlink "$probe/project/dangling")"
printf 'through_omitted_target=%s\n' "$(readlink "$probe/project/through-omitted")"
git -C "$probe" ls-files -v | awk '$1 == "S" {print}'
PROBE="$probe" python - <<'PY'
import os, subprocess
from pathlib import Path
repo = Path(os.environ['PROBE'])
paths = [p.decode() for p in subprocess.check_output(
    ['git', '-C', str(repo), 'ls-files', '-z']).split(b'\0') if p]
print(f'tracked={len(paths)}')
print(f'missing={[p for p in paths if not os.path.lexists(repo / p)]}')
others = subprocess.check_output(
    ['git', '-C', str(repo), 'ls-files', '--others',
     '--exclude-per-directory=.gitignore'], text=True).strip()
print(f"others={others!r}")
ignore = subprocess.check_output(
    ['git', '-C', str(repo), 'show', ':.gitignore'], text=True).strip()
print(f'root_gitignore_from_index={ignore}')
PY
```

Inspection output:

```text
status=''
HEAD_TREE=fc0b1a95339fa7e7255e99d121274746640d1dc6
INDEX_TREE=fc0b1a95339fa7e7255e99d121274746640d1dc6
root_input=load-bearing root input
absolute_out=absent
relative_out=absent
inside_target=app.py
dangling_target=missing.txt
through_omitted_target=../other/absolute-out
S other/absolute-out
S other/relative-out
```

Additional complete-path/ignore inspection:

```text
tracked=13
missing=['other/absolute-out', 'other/relative-out']
others=''
root_gitignore_from_index=*.generated
```

The exact commands for the latter facts were `git ls-files -z` plus `os.path.lexists`, `git ls-files --others --exclude-per-directory=.gitignore`, and `git show :.gitignore`.

### M6 — replacement `write-tree` preserves omitted and ordinary siblings

Command, after applying the same two skip bits/unlinks and updating only `project/app.py`'s cache entry with a new `hash-object -w --stdin` OID in the fresh scratch clone:

```bash
probe=/tmp/b006a-scratch/final-replacement-niLuLz/repo
printf 'BASE_TREE=%s\n' "$(git -C "$probe" rev-parse 'HEAD^{tree}')"
printf 'REPLACEMENT_TREE=%s\n' "$(git -C "$probe" write-tree)"
git -C "$probe" ls-tree "$(git -C "$probe" write-tree)" \
  other/absolute-out other/ordinary.txt project/app.py
printf 'skip_count=%s\n' "$(git -C "$probe" ls-files -v | \
  awk '$1 == "S" {n += 1} END {print n + 0}')"
```

Output:

```text
BASE_TREE=fc0b1a95339fa7e7255e99d121274746640d1dc6
REPLACEMENT_TREE=593a23a7db20b0651068a18a8aacca9548135108
120000 blob 3594e94c04db171e2767224db355f514b13715c5 other/absolute-out
100644 blob c8ca66307ca0e478f551d60cb85a38c04c8c4d81 other/ordinary.txt
100644 blob be082e7aa558da3211b3f6ab6cb7346a53223dc6 project/app.py
skip_count=2
```

### M7 — the command can restore an omitted link and remain clean

Command, in another fresh `/tmp` clone after initially applying the skip bit and unlinking `other/absolute-out`:

```bash
printf "status_before='%s'\n" "$(git status --porcelain=v1)"
printf 'git_show_target=%s\n' "$(git show HEAD:other/absolute-out)"
git update-index --no-skip-worktree -- other/absolute-out
git checkout -q -- other/absolute-out
printf 'restored_target=%s\n' "$(readlink other/absolute-out)"
printf "status_after='%s'\n" "$(git status --porcelain=v1)"
```

Output:

```text
status_before=''
git_show_target=/etc/passwd
restored_target=/etc/passwd
status_after=''
```

This is why the property is initial materialisation, not confinement.

### M8 — both CMRU repository-root dependencies are load-bearing

Control command, from `/tmp/b006a-scratch/cmru-control/cmru`:

```bash
PYTHONPATH=src /usr/local/bin/python -m pytest -q \
  tests/test_cli_dispatch.py::test_checked_in_sample_uses_automatic_release_history \
  tests/test_release_wrapper.py::test_release_wrapper_overwrites_then_appends_with_divider
```

Relevant output:

```text
collected 2 items
tests/test_cli_dispatch.py .                                             [ 50%]
tests/test_release_wrapper.py .                                          [100%]
============================== 2 passed in 0.46s ===============================
```

After deleting only `cmru.project.sample.toml` from a copied tree, command:

```bash
cd /tmp/b006a-scratch/cmru-sample-missing/cmru
test ! -e ../cmru.project.sample.toml
PYTHONPATH=src /usr/local/bin/python -m pytest -q \
  tests/test_cli_dispatch.py::test_checked_in_sample_uses_automatic_release_history
printf 'EXIT=%s\n' "$?"
```

Output:

```text
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/b006a-scratch/cmru-sample-missing/cmru.project.sample.toml'
FAILED tests/test_cli_dispatch.py::test_checked_in_sample_uses_automatic_release_history
============================== 1 failed in 0.57s ===============================
EXIT=1
```

After deleting only `cmru.release.sh` from another copied tree, command:

```bash
cd /tmp/b006a-scratch/cmru-wrapper-missing/cmru
test ! -e ../cmru.release.sh
PYTHONPATH=src /usr/local/bin/python -m pytest -q \
  tests/test_release_wrapper.py::test_release_wrapper_overwrites_then_appends_with_divider
printf 'EXIT=%s\n' "$?"
```

Output:

```text
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/b006a-scratch/cmru-wrapper-missing/cmru.release.sh'
FAILED tests/test_release_wrapper.py::test_release_wrapper_overwrites_then_appends_with_divider
============================== 1 failed in 0.49s ===============================
EXIT=1
```

### M9 — a permanent CMRU R2 gate at this branch would be inconclusive

Command:

```bash
git diff --name-only main...HEAD -- cmru/src
printf 'COUNT=%s\n' "$(git diff --name-only main...HEAD -- cmru/src | wc -l)"
git log -1 --format='%h %s' -- cmru/src
```

Output:

```text
COUNT=0
a230070b fix(cmru): make promotion retry bound explicit
```

This is the measured basis for the controlled source commit in WI-5 rather than permanent CMRU gate adoption.

### M10 — dstdns deleted exactly the real nginx symlink

Read-only commands in `/workspaces/dstdns`:

```bash
git show --format='%h %s' --stat c359a6b1
git ls-tree c359a6b1^ infra-global/reverse-proxy/etc-nginx/modules
git show c359a6b1^:infra-global/reverse-proxy/etc-nginx/modules
```

Output:

```text
c359a6b1 phase0(gate-substrate): drop the one absolute-target symlink assay's snapshot refuses

 infra-global/reverse-proxy/etc-nginx/modules | 1 -
 1 file changed, 1 deletion(-)
120000 blob 4b9b33f1088a3bb0fb692961cd645fda85695af5 infra-global/reverse-proxy/etc-nginx/modules
/usr/lib/nginx/modules
```

### M11 — a safe symlink ancestor is already refused by the output seam

Command:

```bash
probe="$(mktemp -d /tmp/b006a-scratch/artifact-symlink-XXXXXX)"
mkdir -p "$probe/project" "$probe/other"
ln -s ../other "$probe/project/.coverage-out"
PYTHONPATH=assay/src PROBE="$probe" python - <<'PY'
import os
from pathlib import Path
from assay.errors import AssayError
from assay.safeio import reserve_output
root = Path(os.environ['PROBE']) / 'project'
try:
    reserve_output(root, '.coverage-out/coverage.json', limit=1024)
except AssayError as exc:
    print(f'{exc.outcome.value}/{exc.reason_code.value}: {exc}')
else:
    print('unexpected-success')
PY
```

Output:

```text
ERROR/UNREADABLE_ARTIFACT: path component '.coverage-out' is not an existing, non-symlink directory: [Errno 20] Not a directory: '.coverage-out'
```

This supports retaining B006(b)'s `O_NOFOLLOW` rule and adding only the omitted-link collision.

### M12 — current checked-in Assay/CMRU lanes are R0-only

Command:

```bash
rg -n '^rigor = ' --glob 'assay.toml' .
```

Output:

```text
./assay/assay.toml:25:rigor = ["R0"]
./cmru/assay.toml:17:rigor = ["R0"]
```

### M13 — lane-v2 migration surface

Command:

```bash
rg -l 'schema_version = 1' assay/gate assay/tests assay/nyxloom-trove/carve-assets \
  --glob '*.py' --glob '*.toml' | sort
printf 'FILES=%s\n' "$(rg -l 'schema_version = 1' \
  assay/gate assay/tests assay/nyxloom-trove/carve-assets \
  --glob '*.py' --glob '*.toml' | wc -l)"
printf 'OCCURRENCES=%s\n' "$(rg -o 'schema_version = 1' \
  assay/gate assay/tests assay/nyxloom-trove/carve-assets \
  --glob '*.py' --glob '*.toml' | wc -l)"
printf 'CARVE_ASSET_FILES=%s\n' "$(rg -l 'schema_version = 1' \
  assay/nyxloom-trove/carve-assets --glob '*.py' --glob '*.toml' | wc -l)"
```

Output:

```text
assay/gate/python/qualify_topos.py
assay/nyxloom-trove/carve-assets/P21/test_acceptance.py
assay/nyxloom-trove/carve-assets/P23/test_acceptance.py
assay/nyxloom-trove/carve-assets/P24/test_acceptance.py
assay/nyxloom-trove/carve-assets/P25/probe_topos_qualification.py
assay/nyxloom-trove/carve-assets/P26/test_acceptance.py
assay/nyxloom-trove/carve-assets/P33/test_acceptance_v5.py
assay/tests/conftest.py
assay/tests/test_cli_run.py
assay/tests/test_config_accept.py
assay/tests/test_config_env_required.py
assay/tests/test_config_reject.py
assay/tests/test_config_source_roots.py
assay/tests/test_dependency_purity.py
assay/tests/test_distribution_build_release.py
assay/tests/test_self_hosting.py
assay/tests/test_standalone.py
assay/tests/test_verify_layer_independence.py
FILES=18
OCCURRENCES=36
CARVE_ASSET_FILES=6
```

The six carve-asset paths are why WI-1 distinguishes live migration from historical evidence rather than bulk-replacing every match.

### M14 — the frozen CMRU full-suite-green premise is stale

A controlled CMRU probe was built in `/tmp/b006a-scratch/cmru-acceptance`, with the three Topos symlinks removed only in that disposable repository to get past current P22. The exact Assay command was:

```bash
PYTHONPATH=/workspaces/vbpub/.worktrees/assay-B005-B006-coverage-v6/assay/src \
  /usr/local/bin/python -m assay.cli run cmru_b006a_probe \
  --file assay.toml \
  --verdict-json /tmp/b006a-scratch/cmru-cockpit-verdict.json
```

Output:

```text
cmru_b006a_probe: FAIL/COMMAND_FAILED (exit 1)
  commit: fae8ce263adf81ca0f8b3a05164c4ff188c1a68b
  argv: /usr/local/bin/python -m pytest tests -q --cov=src/cmru --cov-branch --cov-report=json:.assay/coverage.json
EXIT=1
VERDICT=FAIL/COMMAND_FAILED
```

Running that argv directly showed the environmental causes and the real suite extent:

```text
ERROR tests/test_agent_controller.py::TestConsulBackend::test_watch_desired_returns_value
ERROR tests/test_agent_controller.py::TestConsulBackend::test_acquire_lock_returns_handle
ERROR tests/test_agent_controller.py::TestConsulBackend::test_publish_observed_ok
FAILED tests/test_release_final_contracts.py::test_release_publish_rejects_response_without_upload_coordinate
============= 1 failed, 1397 passed, 2 skipped, 3 errors in 17.70s =============
EXIT=1
```

Read-only source/history checks:

```bash
wc -l cmru/tests/conftest.py
git log -1 --format='%h %s' -- cmru/tests/test_release_final_contracts.py
sed -n '33,38p' cmru/tests/test_release_final_contracts.py
nl -ba cmru/src/cmru/release.py | sed -n '193,198p'
sed -n '338p' assay/tools/tester-unified-gate.sh
```

Output:

```text
5 cmru/tests/conftest.py
80fbe7e1 test(cmru): cover release REST contracts
def test_release_publish_rejects_response_without_upload_coordinate():
    client = GitHubReleases("owner", "repo", "token")
    client.get_release_by_tag = lambda tag: {"id": 7}
    with pytest.raises(SystemExit) as error:
        client.publish("demo-v1", "title", "notes", [])
    assert error.value.code == 1
   193        elif release.get("id"):
   194            self.update_release(int(release["id"]), title, notes)
   195
   196        rid, upload_url = release.get("id"), release.get("upload_url")
   197        if not rid or not upload_url:
   198            self._fail(f"release {tag} missing id/upload_url", 0, json.dumps(release))
  --network=none \
```

The three setup errors were `PermissionError: [Errno 1] Operation not permitted` on local socket creation and are cockpit-specific. The remaining failure is not: the test replaces `get_release_by_tag` with `{"id": 7}`, after which `GitHubReleases.publish` calls the unstubbed `update_release` before it checks `upload_url`. That call reached `api.github.com` and got `Temporary failure in name resolution`; the registered gate also runs with `--network=none`. There is no global fixture (`cmru/tests/conftest.py` is five import-path lines) that patches it. Thus the brief's assertion that the current full suite is green is stale at input revision `c3b00729...`, likely because the failing test was added in later commit `80fbe7e1`. WI-5 repairs the test boundary explicitly in its disposable qualification baseline; it does not claim an unchanged-current-tree green run.

An earlier disposable historical attempt first produced `ERROR/UNREADABLE_ARTIFACT` because `.assay/` was absent, then `FAIL/COMMAND_FAILED` after a tracked parent was added. Those outcomes are not B006(a) acceptance evidence.

### M15 — the explicit qualification-baseline repair is sufficient for the stale node

Command after adding only `client.update_release = lambda *args, **kwargs: {"id": 7}` to the disposable copy under `/tmp/b006a-scratch/cmru-acceptance`:

```bash
PYTHONPATH=src /usr/local/bin/python -m pytest -q \
  tests/test_release_final_contracts.py::test_release_publish_rejects_response_without_upload_coordinate
printf 'EXIT=%s\n' "$?"
```

Output:

```text
collected 1 item
tests/test_release_final_contracts.py .                                  [100%]
============================== 1 passed in 0.19s ===============================
EXIT=0
```

Without that line, the same node is the single failure shown in M14 and tries to resolve `api.github.com`; this makes the repair differential rather than a deselection.

### M16 — the exact schema path pattern is loadable and differential

Command:

```bash
python - <<'PY'
import json, re
encoded = r'"^(?!(?:\\.{1,2}|\\.git)(?:/|$))[^/\\\\\\u0000]+(?:/(?!(?:\\.{1,2}|\\.git)(?:/|$))[^/\\\\\\u0000]+)*$"'
pattern = re.compile(json.loads(encoded))
cases = {
    'topos/x': True, 'a/.gitkeep': True, 'é/x': True,
    '': False, '/abs': False, 'a//b': False, 'a/': False,
    './a': False, 'a/../b': False, 'a/.git/b': False,
    r'a\\b': False, 'a\x00b': False,
}
for raw, expected in cases.items():
    got = pattern.fullmatch(raw) is not None
    print(f'{raw!r}: got={got} expected={expected}')
    assert got is expected
print('PATTERN_MATRIX=PASS')
PY
```

Output:

```text
'topos/x': got=True expected=True
'a/.gitkeep': got=True expected=True
'é/x': got=True expected=True
'': got=False expected=False
'/abs': got=False expected=False
'a//b': got=False expected=False
'a/': got=False expected=False
'./a': got=False expected=False
'a/../b': got=False expected=False
'a/.git/b': got=False expected=False
'a\\\\b': got=False expected=False
'a\x00b': got=False expected=False
PATTERN_MATRIX=PASS
```

The command exits nonzero on the first mismatch; removing either component lookahead or the excluded-character class changes at least one row.

### M17 — direct `Lane` constructor migration surface

Command:

```bash
rg -l 'Lane\(' assay/tests | sort
```

Output:

```text
assay/tests/conftest.py
assay/tests/test_canary_python_pipeline.py
```

This is the measured basis for WI-1/WI-3 naming those two existing test files rather than an open-ended “fix callers” instruction.

### M18 — locked lane-v1 node inventory for the independently green v2 commit

Command:

```bash
python - <<'PY'
import ast
from pathlib import Path
for rel, helper in [
    ('assay/nyxloom-trove/carve-assets/P26/test_acceptance.py', '_lane_document'),
    ('assay/nyxloom-trove/carve-assets/P33/test_acceptance_v5.py', '_load_lane'),
]:
    tree = ast.parse(Path(rel).read_text(encoding='utf-8'))
    print(f'{rel} -> {helper}')
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
            if any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                   and child.func.id == helper for child in ast.walk(node)):
                print(node.name)
PY
```

Output:

```text
assay/nyxloom-trove/carve-assets/P26/test_acceptance.py -> _lane_document
test_runner_binds_evidence_batch_to_lane_source_before_any_work
test_r0_attestation_config_round_trips_without_inventing_a_judge
test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape
test_cli_emits_the_complete_hand_authored_v4_artifact
test_cli_preserves_independent_malformed_missing_and_current_evidence
test_attestation_timeout_is_atomic_and_does_not_run_a_failing_command
test_direct_r0_uses_the_existing_deadline_remainder_not_a_fresh_budget
assay/nyxloom-trove/carve-assets/P33/test_acceptance_v5.py -> _load_lane
test_config_fixture_itself_loads_today
test_config_refuses_a_cross_language_operator
test_config_accepts_a_matching_language_operator
test_config_names_kill_signal_artifact_as_reserved_for_p34
test_config_names_equivalence_artifact_as_reserved_for_p34
```

The three P26 CLI/artifact nodes in the middle are already deselected for the prior verdict hard cut; the remaining four P26 and five P33 nodes are the exact WI-1 addition. Runtime measurement after implementation remains the gate: AST inventory identifies coupling but does not excuse any unexpected red.

### M19 — direct `SnapshotSpec` constructor surface

Command:

```bash
rg -l '(\bSnapshotSpec|\.SnapshotSpec)\(' assay --glob '*.py' | sort
```

Output:

```text
assay/nyxloom-trove/carve-assets/P22/test_acceptance.py
assay/nyxloom-trove/carve-assets/P23/probe_reexecution_contract.py
assay/src/assay/runner.py
assay/tests/conftest.py
assay/tests/test_isolation.py
```

The first two are frozen historical assets and are not edited; the latter three are WI-2's complete required-field migration set.
